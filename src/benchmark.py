"""Measures query performance with EXPLAIN ANALYZE and writes docs/BENCHMARKS.md.

Compares, on the live database:
  1. B-tree index vs forced sequential scan
  2. Partial index (anomalies only) vs full scan
  3. Plain view vs materialized view
  4. Year-partitioned table (partition pruning) vs normal table
Every measurement runs inside a transaction that is rolled back, so nothing changes.
Usage:  python src/benchmark.py"""
import datetime as dt
import statistics
from pathlib import Path

from db import get_conn

RUNS = 7
OUT = Path(__file__).resolve().parent.parent / "docs" / "BENCHMARKS.md"
NO_INDEX = ("SET LOCAL enable_indexscan = off",
            "SET LOCAL enable_bitmapscan = off",
            "SET LOCAL enable_indexonlyscan = off")


def plan_nodes(plan, acc=None):
    """Flatten a JSON plan into short labels like 'Index Scan (daily_prices_pkey)'."""
    acc = [] if acc is None else acc
    label = plan["Node Type"]
    if plan.get("Index Name"):
        label += f" ({plan['Index Name']})"
    elif plan.get("Relation Name"):
        label += f" ({plan['Relation Name']})"
    if "Scan" in plan["Node Type"]:
        acc.append(label)
    for child in plan.get("Plans", []):
        plan_nodes(child, acc)
    return acc


def measure(conn, sql, setup=()):
    """Median execution time (ms) over RUNS (first warm-up run discarded) + scan nodes."""
    times, nodes = [], []
    for _ in range(RUNS + 1):
        with conn.transaction(force_rollback=True):
            for s in setup:
                conn.execute(s)
            p = conn.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql).fetchone()[0][0]
        times.append(p["Execution Time"])
        nodes = plan_nodes(p["Plan"])
    return statistics.median(times[1:]), nodes


def summarise_nodes(nodes):
    uniq = sorted(set(nodes))
    if len(uniq) > 3:  # partitioned scans list every partition
        return f"{len(nodes)} scans: " + ", ".join(uniq[:2]) + ", …"
    return ", ".join(uniq)


def build_partitioned_copy(conn):
    """Year-partitioned copy of daily_prices used only for this comparison."""
    y0, y1 = conn.execute(
        "SELECT EXTRACT(YEAR FROM MIN(price_date))::int, EXTRACT(YEAR FROM MAX(price_date))::int FROM daily_prices"
    ).fetchone()
    ddl = ["DROP TABLE IF EXISTS daily_prices_part CASCADE",
           """CREATE TABLE daily_prices_part
                (LIKE daily_prices INCLUDING DEFAULTS INCLUDING CONSTRAINTS)
                PARTITION BY RANGE (price_date)"""]
    for y in range(y0, y1 + 2):
        ddl.append(f"CREATE TABLE daily_prices_part_y{y} PARTITION OF daily_prices_part "
                   f"FOR VALUES FROM ('{y}-01-01') TO ('{y + 1}-01-01')")
    ddl += ["ALTER TABLE daily_prices_part ADD PRIMARY KEY (ticker, price_date)",
            "INSERT INTO daily_prices_part SELECT * FROM daily_prices",
            "ANALYZE daily_prices_part"]
    with conn.transaction():
        for s in ddl:
            conn.execute(s)
    return y1 - y0 + 2, ddl


def main():
    with get_conn() as conn:
        conn.autocommit = True
        conn.execute("ANALYZE")
        n_parts, part_ddl = build_partitioned_copy(conn)
        last_full_year = dt.date.today().year - 1

        tests = [
            ("B-tree index: one stock, last 12 months",
             "SELECT price_date, close FROM daily_prices "
             "WHERE ticker = 'INFY.NS' AND price_date >= CURRENT_DATE - 365",
             NO_INDEX, (), "Forced sequential scan", "Primary-key index"),
            ("Partial index: anomalies, last 90 days",
             "SELECT ticker, signal_date, anomaly_reason FROM signals "
             "WHERE is_anomaly AND signal_date >= CURRENT_DATE - 90",
             NO_INDEX, (), "Forced sequential scan", "idx_signals_anomaly (partial)"),
            ("Materialized view: sector stats, last 12 months",
             "SELECT * FROM v_sector_daily WHERE price_date >= CURRENT_DATE - 365",
             (), (), "Plain view (aggregates every time)", None),
            ("Partitioning: one year, average close per stock",
             "SELECT ticker, AVG(close) FROM daily_prices "
             f"WHERE price_date >= '{last_full_year}-01-01' AND price_date < '{last_full_year + 1}-01-01' "
             "GROUP BY ticker",
             NO_INDEX, NO_INDEX, "Normal table (scans all years)", None),
        ]
        alt_sql = {
            2: "SELECT * FROM mv_sector_daily WHERE price_date >= CURRENT_DATE - 365",
            3: tests[3][1].replace("FROM daily_prices", "FROM daily_prices_part"),
        }
        alt_label = {2: "Materialized view (precomputed)",
                     3: f"Year-partitioned table (pruned to 1 of {n_parts} partitions)"}

        rows = []
        for i, (name, sql, setup_a, setup_b, label_a, label_b) in enumerate(tests):
            t_a, n_a = measure(conn, sql, setup_a)
            t_b, n_b = measure(conn, alt_sql.get(i, sql), setup_b or (NO_INDEX if i == 3 else ()))
            rows.append((name, label_a, t_a, n_a, label_b or alt_label[i], t_b, n_b))
            print(f"{name}: {t_a:.2f} ms -> {t_b:.2f} ms")

        sizes = conn.execute("""
            SELECT c.relname, c.reltuples::bigint, pg_size_pretty(pg_total_relation_size(c.oid))
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind IN ('r','m')
              AND c.relname NOT LIKE 'daily_prices_part%'
            ORDER BY pg_total_relation_size(c.oid) DESC""").fetchall()
        signal_ms, _ = measure(conn, "SELECT refresh_signals(CURRENT_DATE - 30)")
        version = conn.execute("SHOW server_version").fetchone()[0].split()[0]

    lines = [
        "# Query performance benchmarks",
        "",
        f"_Generated automatically by `src/benchmark.py` on {dt.date.today():%d %b %Y} "
        f"(PostgreSQL {version} on Neon, free tier). Median of {RUNS} runs after a warm-up; "
        "every run is wrapped in a rolled-back transaction._",
        "",
        "| Test | Before | After | Speed-up |",
        "|---|---|---|---|",
    ]
    for name, la, ta, _, lb, tb, _ in rows:
        speed = f"**{ta / tb:.1f}×**" if tb > 0 else "–"
        lines.append(f"| {name} | {la}: {ta:.2f} ms | {lb}: {tb:.2f} ms | {speed} |")
    lines += ["", "## Query plans used", ""]
    for name, la, _, na, lb, _, nb in rows:
        lines += [f"**{name}**", f"- {la}: {summarise_nodes(na)}", f"- {lb}: {summarise_nodes(nb)}", ""]
    lines += [
        "## Signal engine",
        "",
        f"`refresh_signals()` recomputes 30 days of signals for all stocks in **{signal_ms:.0f} ms** "
        "(window functions over ~400 days of look-back per stock).",
        "",
        "## Table sizes",
        "",
        "| Table | Rows (approx.) | Size on disk |",
        "|---|---|---|",
    ]
    lines += [f"| `{n}` | {r:,} | {s} |" for n, r, s in sizes]
    lines += [
        "",
        "## How the partitioned table is built",
        "",
        "```sql",
        *[s.strip() + ";" for s in part_ddl[:4]],
        "-- ... one partition per year ...",
        *[s.strip() + ";" for s in part_ddl[-3:]],
        "```",
        "",
        "## Notes",
        "",
        "- At ~73k rows the whole database fits in memory, so absolute times are small; "
        "the speed-ups show how each technique scales as data grows (e.g. to all ~2,000 NSE stocks).",
        "- Partitioning lets PostgreSQL skip every year except the one queried (*partition pruning*), "
        "without needing an index — so indexes are disabled for both sides of that test. "
        "The production table is not partitioned yet: at this size the date index gives similar speed; "
        "`daily_prices_part` is a benchmark copy showing how the table would scale.",
        "- The materialized view is refreshed with `REFRESH MATERIALIZED VIEW CONCURRENTLY` after each "
        "daily load, so dashboards never wait for aggregation and readers are never blocked.",
    ]
    OUT.write_text("\n".join(lines) + "\n")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()

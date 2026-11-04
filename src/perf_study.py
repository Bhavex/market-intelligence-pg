import argparse
import json
import os
import platform
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg

from scripts.load_sample_data import assert_throwaway

QUERIES = {
    "company_history_1y": {
        "used_by": "dashboard Company detail; GET /v1/companies/{ticker}/signals",
        "sql": """SELECT v.*, s.high_52w, s.breakout_52w FROM v_company_daily v
                  LEFT JOIN signals s ON s.ticker = v.ticker AND s.signal_date = v.price_date
                  WHERE v.ticker = %(t)s AND v.price_date BETWEEN %(d1y)s AND %(d)s ORDER BY v.price_date""",
    },
    "api_prices_page": {
        "used_by": "GET /v1/companies/{ticker}/prices?order=desc (page 1 of 100)",
        "sql": """SELECT price_date, open, high, low, close, adj_close, volume FROM daily_prices
                  WHERE ticker = %(t)s ORDER BY price_date DESC LIMIT 100 OFFSET 0""",
    },
    "api_prices_count": {
        "used_by": "pagination total for the prices endpoint",
        "sql": "SELECT COUNT(*) FROM daily_prices WHERE ticker = %(t)s AND price_date BETWEEN %(d1y)s AND %(d)s",
    },
    "all_stocks_one_day": {
        "used_by": "cross-section for one session (sector strength, data-quality checks)",
        "sql": "SELECT ticker, close, volume FROM daily_prices WHERE price_date = %(d)s",
    },
    "latest_prices_by_sector": {
        "used_by": "dashboard Sector overview; GET /v1/companies/{ticker}",
        "sql": "SELECT * FROM v_latest_prices_by_sector",
    },
    "anomalies_180d": {
        "used_by": "dashboard Anomalies page",
        "sql": """SELECT signal_date, ticker, return_1d, anomaly_reason FROM signals
                  WHERE is_anomaly AND signal_date >= %(d180)s ORDER BY signal_date DESC""",
    },
    "sector_daily_1y_view": {
        "used_by": "v_sector_daily (plain view, aggregates on every query)",
        "sql": "SELECT * FROM v_sector_daily WHERE price_date >= %(d1y)s",
    },
    "sector_daily_1y_matview": {
        "used_by": "mv_sector_daily (materialized copy, refreshed after each load)",
        "sql": "SELECT * FROM mv_sector_daily WHERE price_date >= %(d1y)s",
    },
    "sector_strength_latest": {
        "used_by": "GET /v1/sectors/strength (default: latest day)",
        "sql": "SELECT * FROM v_sector_strength WHERE price_date = %(d)s",
    },
    "freshness": {
        "used_by": "dashboard Pipeline health; GET /v1/pipeline/status",
        "sql": "SELECT * FROM v_data_freshness",
    },
}

SCENARIOS = {
    "indexed": [],
    "no_secondary_indexes": [
        "DROP INDEX idx_daily_prices_date", "DROP INDEX idx_signals_date", "DROP INDEX idx_signals_anomaly",
        "DROP INDEX idx_signals_breakout", "DROP INDEX ux_mv_sector_daily",
    ],
    "no_indexes_at_all": [
        "DROP INDEX idx_daily_prices_date", "DROP INDEX idx_signals_date", "DROP INDEX idx_signals_anomaly",
        "DROP INDEX idx_signals_breakout", "DROP INDEX ux_mv_sector_daily",
        "ALTER TABLE signals DROP CONSTRAINT signals_pkey CASCADE",
        "ALTER TABLE daily_prices DROP CONSTRAINT daily_prices_pkey CASCADE",
    ],
}


def plan_nodes(plan: dict) -> list:
    out = [plan]
    for child in plan.get("Plans", []):
        out.extend(plan_nodes(child))
    return out


def describe(plan: dict) -> str:
    seen = []
    for n in plan_nodes(plan):
        if "Scan" in n["Node Type"]:
            label = n["Node Type"].replace("Index Only Scan", "Index-only scan").replace("Index Scan", "Index scan") \
                .replace("Bitmap Heap Scan", "Bitmap heap scan").replace("Bitmap Index Scan", "Bitmap index scan") \
                .replace("Seq Scan", "Seq scan")
            target = n.get("Index Name") or n.get("Relation Name") or ""
            item = f"{label} ({target})"
            if item not in seen:
                seen.append(item)
    return ", ".join(seen[:3]) + (" …" if len(seen) > 3 else "")


def measure(conn, sql: str, params: dict, runs: int, timeout_s: int) -> dict:
    explain = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql
    times, planning, buffers = [], [], []
    plan = None
    for i in range(runs + 1):
        try:
            with conn.transaction():
                conn.execute(f"SET LOCAL statement_timeout = '{timeout_s}s'")
                doc = conn.execute(explain, params).fetchone()[0][0]
        except psycopg.errors.QueryCanceled:
            with conn.transaction():
                doc = conn.execute("EXPLAIN (FORMAT JSON) " + sql, params).fetchone()[0][0]
            return {"exec_ms": None, "timed_out_after_s": timeout_s, "plan_ms": None, "buffers": None,
                    "rows": None, "plan": describe(doc["Plan"])}
        if i == 0:
            continue
        plan = doc["Plan"]
        times.append(doc["Execution Time"])
        planning.append(doc["Planning Time"])
        buffers.append(plan.get("Shared Hit Blocks", 0) + plan.get("Shared Read Blocks", 0))
        if doc["Execution Time"] > 5000 and len(times) >= 3:
            break
    return {"exec_ms": statistics.median(times), "plan_ms": statistics.median(planning),
            "buffers": int(statistics.median(buffers)), "rows": plan.get("Actual Rows"), "plan": describe(plan)}


def study(url: str, runs: int, ticker: str, timeout_s: int = 60) -> dict:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("VACUUM (ANALYZE)")
        d = conn.execute("SELECT MAX(price_date) FROM daily_prices").fetchone()[0]
        counts = conn.execute("""SELECT (SELECT COUNT(*) FROM daily_prices), (SELECT COUNT(DISTINCT ticker) FROM daily_prices),
                                        (SELECT COUNT(*) FROM signals), MIN(price_date), MAX(price_date),
                                        pg_size_pretty(pg_total_relation_size('daily_prices')),
                                        pg_size_pretty(pg_total_relation_size('signals'))
                                 FROM daily_prices""").fetchone()
        version = conn.execute("SHOW server_version").fetchone()[0]
    from datetime import timedelta
    params = {"t": ticker, "d": d, "d1y": d - timedelta(days=365), "d180": d - timedelta(days=180)}
    results = {}
    with psycopg.connect(url) as conn:
        conn.execute("SET max_parallel_workers_per_gather = 0")
        conn.execute("SET jit = off")
        for scenario, ddl in SCENARIOS.items():
            for stmt in ddl:
                conn.execute(stmt)
            results[scenario] = {name: measure(conn, q["sql"], params, runs, timeout_s) for name, q in QUERIES.items()}
            print(f"{scenario}: done", flush=True)
            conn.rollback()
            conn.execute("SET max_parallel_workers_per_gather = 0")
            conn.execute("SET jit = off")
    return {"server_version": version, "machine": f"{platform.system()} {platform.machine()}", "runs": runs,
            "rows_daily_prices": counts[0], "tickers": counts[1], "rows_signals": counts[2],
            "first_date": str(counts[3]), "last_date": str(counts[4]), "size_daily_prices": counts[5],
            "size_signals": counts[6], "ticker": ticker, "results": results}


def fmt(m: dict) -> str:
    return f"> {m['timed_out_after_s'] * 1000:,} ms (timed out)" if m["exec_ms"] is None else f"{m['exec_ms']:,.2f} ms"


def speedup(fast: dict, slow: dict) -> str:
    if fast["exec_ms"] is None:
        return "–"
    if slow["exec_ms"] is None:
        return f"> {slow['timed_out_after_s'] * 1000 / fast['exec_ms']:,.0f}×"
    return f"{slow['exec_ms'] / fast['exec_ms']:,.1f}×"


def markdown(res: dict) -> str:
    r = res["results"]
    lines = ["| Query | Used by | Indexed | No secondary indexes | No indexes at all | Speed-up vs no indexes | Plan with indexes |",
             "|---|---|---:|---:|---:|---:|---|"]
    for name, q in QUERIES.items():
        a, b, c = r["indexed"][name], r["no_secondary_indexes"][name], r["no_indexes_at_all"][name]
        lines.append(f"| `{name}` | {q['used_by']} | {fmt(a)} | {fmt(b)} | {fmt(c)} | **{speedup(a, c)}** | {a['plan']} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="EXPLAIN ANALYZE the dashboard and API queries with and without indexes")
    ap.add_argument("--runs", type=int, default=9)
    ap.add_argument("--ticker", default="INFY.NS")
    ap.add_argument("--label", default="study")
    ap.add_argument("--timeout", type=int, default=60, help="seconds before a single query is cancelled")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "performance")
    args = ap.parse_args()
    url = os.environ.get("PERF_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("set PERF_DATABASE_URL to a throwaway database loaded with scripts/load_sample_data.py")
    assert_throwaway(url)
    res = study(url, args.runs, args.ticker, args.timeout)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"{args.label}.json").write_text(json.dumps(res, indent=2, default=str))
    print(f"{res['rows_daily_prices']} price rows, {res['tickers']} tickers, PostgreSQL {res['server_version']}")
    print(markdown(res))


if __name__ == "__main__":
    main()

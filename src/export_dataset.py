"""Export the open research dataset: "How trustworthy is free end-of-day data for NSE stocks?"

Only DERIVED measurements are published: differences in basis points, revision kinds and timings,
agreement flags, missing-session lists and simulation results. Raw price series from Yahoo Finance
or the exchanges are NOT included (their terms restrict redistribution); anyone can rebuild them
with this repository's code.

Usage:  python src/export_dataset.py [--out dataset]
Writes CSV files, a JSON file, DATASHEET.md and SHA256SUMS into the output folder.
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path

from db import get_conn

TABLES = {
    "universe.csv": """
        SELECT c.ticker, c.company_name, s.sector_name AS sector_or_industry, c.cap_group, c.in_dashboard,
               array_to_string(c.index_membership, ';') AS index_membership, c.isin
        FROM companies c JOIN sectors s ON s.sector_id = c.sector_id
        WHERE c.is_active ORDER BY c.ticker""",
    "revisions.csv": """
        SELECT ticker, price_date, revision_kind, old_known_from, revised_at,
               ROUND(EXTRACT(EPOCH FROM revised_after_session) / 3600, 2) AS revised_hours_after_session,
               close_change_bp, adj_change_bp,
               old_source, new_source,
               CASE WHEN old_volume > 0 THEN ROUND((new_volume::numeric / old_volume - 1) * 100, 3) END AS volume_change_pct
        FROM v_price_revisions ORDER BY ticker, price_date, revised_at""",
    "settlement.csv": """
        SELECT ticker, price_date, n_versions, first_seen, last_changed,
               ROUND(EXTRACT(EPOCH FROM settled_after_session) / 3600, 2) AS settled_hours_after_session,
               first_to_final_bp
        FROM v_settlement ORDER BY ticker, price_date""",
    "agreement_yahoo_ns_vs_nse.csv": """
        SELECT ticker, price_date, diff_bp, agrees, matches_last_trade_instead, volume_diff_pct,
               first_print_diff_bp, first_print_agrees, live_captured, scale_mismatch, yahoo_source
        FROM v_source_agreement WHERE source = 'NSE' ORDER BY ticker, price_date""",
    "agreement_yahoo_ns_vs_bse.csv": """
        SELECT ticker, price_date, diff_bp, agrees, scale_mismatch
        FROM v_source_agreement WHERE source = 'BSE' ORDER BY ticker, price_date""",
    "agreement_yahoo_bo_vs_bse.csv": """
        SELECT ticker, price_date, diff_bp, agrees, first_print_agrees, live_captured, scale_mismatch, n_changes
        FROM v_feed_agreement ORDER BY ticker, price_date""",
    "missing_sessions.csv": """
        SELECT r.ticker, r.price_date
        FROM reference_prices r
        JOIN (SELECT ticker, MIN(price_date) AS first_day FROM daily_prices GROUP BY ticker) d
             ON d.ticker = r.ticker AND r.price_date >= d.first_day
        LEFT JOIN daily_prices p ON p.ticker = r.ticker AND p.price_date = r.price_date
        WHERE r.source = 'NSE' AND p.ticker IS NULL ORDER BY r.ticker, r.price_date""",
    "official_file_log.csv": """
        SELECT source, file_date, status, http_status, rows_matched, fetched_at
        FROM reference_fetch_log ORDER BY source, file_date""",
    "quality_by_group.csv": "SELECT * FROM v_quality_by_group",
    "fragility_results.csv": """
        SELECT r.frag_run_id, f.data_through, r.universe, r.scenario, r.signal, r.n_compared, r.n_flipped,
               r.flip_rate, r.ci_low, r.ci_high
        FROM fragility_results r JOIN fragility_runs f USING (frag_run_id)
        WHERE r.frag_run_id = (SELECT MAX(frag_run_id) FROM fragility_runs)
        ORDER BY r.universe, r.scenario, r.signal""",
    "backtest_sensitivity.csv": """
        SELECT scenario, metric, value FROM backtest_sensitivity
        WHERE frag_run_id = (SELECT MAX(frag_run_id) FROM fragility_runs) ORDER BY scenario, metric""",
}


def cell(v):
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dataset")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    with get_conn() as conn:
        for name, sql in TABLES.items():
            cur = conn.execute(sql)
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
            with open(out / name, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                w.writerows([[cell(v) for v in r] for r in rows])
            counts[name] = (len(rows), cols)
            print(f"{name}: {len(rows)} rows")
        run = conn.execute("""SELECT frag_run_id, created_at, data_through, n_stock_days, seed, params, validation
                              FROM fragility_runs ORDER BY frag_run_id DESC LIMIT 1""").fetchone()
        meta = conn.execute("""SELECT (SELECT MIN(price_date) FROM daily_prices), (SELECT MAX(price_date) FROM daily_prices),
                                      (SELECT MIN(sys_from) FROM price_versions)""").fetchone()
    if run:
        (out / "fragility_run.json").write_text(json.dumps(
            {"frag_run_id": run[0], "created_at": cell(run[1]), "data_through": cell(run[2]), "n_stock_days": run[3],
             "seed": run[4], "params": run[5], "validation": run[6]}, indent=1, default=str))
    today = dt.date.today().isoformat()
    lines = [
        "# Datasheet: free end-of-day data quality for NSE stocks",
        "",
        f"Version {today}. Produced by `src/export_dataset.py` of "
        "https://github.com/Bhavex/market-intelligence-pg (method: docs/TRUST.md).",
        "",
        "## What it is",
        "Measurements of how a free price feed (Yahoo Finance, NSE `.NS` and BSE `.BO` listings) differs from the "
        "exchanges' official end-of-day files (NSE and BSE bhavcopy), how often and how its values are revised "
        "after first publication, which trading sessions it misses, and how often common technical signals "
        "change their answer under realistic data errors (simulation results).",
        "",
        "## What it is not",
        "No raw price series are included. Yahoo Finance and the exchanges restrict redistribution of their data; "
        "all published values are differences (basis points / percent), flags, timestamps and counts. The prices "
        "can be rebuilt with the repository's code.",
        "",
        "## Coverage",
        f"* Price history in the source database: {cell(meta[0])} to {cell(meta[1])}.",
        f"* Version history (first prints and revisions) captured live since {cell(meta[2])}.",
        "* Stocks: the 25-stock dashboard universe, every NIFTY 50 member, and 25 NIFTY Smallcap 100 members "
        "(every 4th by symbol) — see `universe.csv`.",
        "",
        "## Files",
        "| File | Rows | Columns |",
        "|---|---:|---|",
    ]
    lines += [f"| `{n}` | {c[0]} | {', '.join(c[1])} |" for n, c in counts.items()]
    lines += [
        "",
        "## Definitions",
        "* `diff_bp`: (free close / official close − 1) × 10,000.",
        "* `agrees`: within one tick (₹0.05) or 1 bp of the official close, whichever is larger.",
        "* `live_captured`: the first version was recorded within 3 days of the session (a real first print, not backfill).",
        "* `scale_mismatch`: ratio outside 0.8–1.25 — a split/bonus has rescaled the free history; not comparable.",
        "* `revision_kind`: rule-based class of each change (docs/TRUST.md §2.2).",
        "* Fragility `flip_rate`: share of comparisons whose signal answer changed; `ci_low`/`ci_high`: 95% "
        "cluster-robust interval (cluster = contaminated day × simulation).",
        "",
        "## Known limitations",
        "Short live-capture window; the intraday error model is a Brownian bridge inside the day's range; "
        "25 small caps are a sample; one free provider. See docs/TRUST.md §4.",
        "",
        "## Licence",
        "The derived data in this folder: CC BY 4.0. Please cite the repository (CITATION.cff).",
    ]
    (out / "DATASHEET.md").write_text("\n".join(lines) + "\n")
    sums = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(out.iterdir())
            if p.is_file() and p.name != "SHA256SUMS"]
    (out / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(f"dataset written to {out}/ ({len(sums)} files)")


if __name__ == "__main__":
    main()

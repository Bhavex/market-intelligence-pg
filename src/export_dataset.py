"""Export the open research dataset: "How trustworthy is free end-of-day data for NSE stocks?"

Only DERIVED measurements are published: differences in basis points, revision kinds and timings,
agreement flags, missing-session lists and simulation results. Raw price series from Yahoo Finance
or the exchanges are NOT included (their terms restrict redistribution); anyone can rebuild them
with this repository's code.

Usage:  python src/export_dataset.py [--out dataset] [--cutoff 2026-10-19] [--frag-run-id N]
        --cutoff keeps only rows with price_date (file_date) up to that day, so the files match a study pinned to it;
        --frag-run-id picks the fragility run to publish (default: the latest). Reads only: nothing is written to the database.
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


# v_quality_by_group (sql/11_research.sql) with every base table cut at %(cutoff)s (NULL = no cut: identical to the view).
QUALITY_BY_GROUP = """
WITH g AS (
    SELECT ticker, 'dashboard'::text AS grp FROM companies WHERE is_active AND in_dashboard
    UNION ALL SELECT ticker, 'large' FROM companies WHERE is_active AND cap_group = 'large'
    UNION ALL SELECT ticker, 'small' FROM companies WHERE is_active AND cap_group = 'small'
),
days AS (
    SELECT ticker, COUNT(*) AS n_days, MIN(price_date) AS first_day FROM daily_prices
    WHERE (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date) GROUP BY ticker
),
nse AS (
    SELECT ticker, COUNT(*) AS n, COUNT(*) FILTER (WHERE agrees) AS n_agree,
           COUNT(*) FILTER (WHERE live_captured) AS n_live,
           COUNT(*) FILTER (WHERE live_captured AND first_print_agrees) AS n_live_agree
    FROM v_source_agreement WHERE source = 'NSE' AND NOT scale_mismatch
      AND (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date) GROUP BY ticker
),
bse AS (
    SELECT ticker, COUNT(*) AS n, COUNT(*) FILTER (WHERE agrees) AS n_agree,
           COUNT(*) FILTER (WHERE live_captured) AS n_live,
           COUNT(*) FILTER (WHERE live_captured AND first_print_agrees) AS n_live_agree
    FROM v_feed_agreement WHERE NOT scale_mismatch
      AND (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date) GROUP BY ticker
),
miss AS (
    SELECT r.ticker, COUNT(*) AS n_traded, COUNT(*) FILTER (WHERE p.ticker IS NULL) AS n_missing
    FROM reference_prices r
    JOIN days d ON d.ticker = r.ticker AND r.price_date >= d.first_day
    LEFT JOIN daily_prices p ON p.ticker = r.ticker AND p.price_date = r.price_date
    WHERE r.source = 'NSE' AND (%(cutoff)s::date IS NULL OR r.price_date <= %(cutoff)s::date)
    GROUP BY r.ticker
),
rev AS (
    SELECT ticker,
           COUNT(*) FILTER (WHERE revision_kind NOT IN ('dividend_adjustment', 'split_or_bonus_adjustment', 'deleted')) AS n_value_rev,
           COUNT(*) FILTER (WHERE revision_kind IN ('dividend_adjustment', 'split_or_bonus_adjustment')) AS n_rescaled
    FROM v_price_revisions WHERE (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date) GROUP BY ticker
)
SELECT g.grp,
       COUNT(*)                                                         AS n_stocks,
       SUM(days.n_days)                                                 AS n_stock_days,
       SUM(nse.n)                                                       AS nse_compared,
       ROUND(100.0 * SUM(nse.n_agree) / NULLIF(SUM(nse.n), 0), 3)       AS nse_agree_pct,
       SUM(nse.n_live)                                                  AS nse_live_first_prints,
       ROUND(100.0 * SUM(nse.n_live_agree) / NULLIF(SUM(nse.n_live), 0), 2) AS nse_first_print_agree_pct,
       SUM(bse.n)                                                       AS bse_feed_compared,
       ROUND(100.0 * SUM(bse.n_agree) / NULLIF(SUM(bse.n), 0), 3)       AS bse_feed_agree_pct,
       SUM(bse.n_live)                                                  AS bse_feed_live_first_prints,
       ROUND(100.0 * SUM(bse.n_live_agree) / NULLIF(SUM(bse.n_live), 0), 2) AS bse_feed_first_print_agree_pct,
       SUM(miss.n_traded)                                               AS sessions_traded,
       SUM(miss.n_missing)                                              AS sessions_missing_in_yahoo,
       ROUND(100.0 * SUM(miss.n_missing) / NULLIF(SUM(miss.n_traded), 0), 3) AS missing_pct,
       ROUND(1000.0 * COALESCE(SUM(rev.n_value_rev), 0) / NULLIF(SUM(days.n_days), 0), 3) AS value_revisions_per_1000,
       COALESCE(SUM(rev.n_rescaled), 0)                                 AS rescaled_rows
FROM g
LEFT JOIN days USING (ticker) LEFT JOIN nse USING (ticker) LEFT JOIN bse USING (ticker)
LEFT JOIN miss USING (ticker) LEFT JOIN rev USING (ticker)
GROUP BY g.grp"""

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
        FROM v_price_revisions WHERE (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date) ORDER BY ticker, price_date, revised_at""",
    "settlement.csv": """
        SELECT ticker, price_date, n_versions, first_seen, last_changed,
               ROUND(EXTRACT(EPOCH FROM settled_after_session) / 3600, 2) AS settled_hours_after_session,
               first_to_final_bp
        FROM v_settlement WHERE (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date) ORDER BY ticker, price_date""",
    "agreement_yahoo_ns_vs_nse.csv": """
        SELECT ticker, price_date, diff_bp, agrees, matches_last_trade_instead, volume_diff_pct,
               first_print_diff_bp, first_print_agrees, live_captured, scale_mismatch, yahoo_source
        FROM v_source_agreement WHERE source = 'NSE' AND (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date) ORDER BY ticker, price_date""",
    "agreement_yahoo_ns_vs_bse.csv": """
        SELECT ticker, price_date, diff_bp, agrees, scale_mismatch
        FROM v_source_agreement WHERE source = 'BSE' AND (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date) ORDER BY ticker, price_date""",
    "agreement_yahoo_bo_vs_bse.csv": """
        SELECT ticker, price_date, diff_bp, agrees, first_print_agrees, live_captured, scale_mismatch, n_changes
        FROM v_feed_agreement WHERE (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date) ORDER BY ticker, price_date""",
    "missing_sessions.csv": """
        SELECT r.ticker, r.price_date
        FROM reference_prices r
        JOIN (SELECT ticker, MIN(price_date) AS first_day FROM daily_prices GROUP BY ticker) d
             ON d.ticker = r.ticker AND r.price_date >= d.first_day
        LEFT JOIN daily_prices p ON p.ticker = r.ticker AND p.price_date = r.price_date
        WHERE r.source = 'NSE' AND p.ticker IS NULL AND (%(cutoff)s::date IS NULL OR r.price_date <= %(cutoff)s::date) ORDER BY r.ticker, r.price_date""",
    "official_file_log.csv": """
        SELECT source, file_date, status, http_status, rows_matched, fetched_at
        FROM reference_fetch_log WHERE (%(cutoff)s::date IS NULL OR file_date <= %(cutoff)s::date) ORDER BY source, file_date""",
    # the view v_quality_by_group (sql/11_research.sql) has no date filter, so its text is repeated here with one
    "quality_by_group.csv": QUALITY_BY_GROUP,
    "fragility_results.csv": """
        SELECT r.frag_run_id, f.data_through, r.universe, r.scenario, r.signal, r.n_compared, r.n_flipped,
               r.flip_rate, r.ci_low, r.ci_high, r.boot_low, r.boot_high
        FROM fragility_results r JOIN fragility_runs f USING (frag_run_id)
        WHERE r.frag_run_id = COALESCE(%(frag)s, (SELECT MAX(frag_run_id) FROM fragility_runs))
        ORDER BY r.universe, r.scenario, r.signal""",
    "backtest_sensitivity.csv": """
        SELECT scenario, metric, value FROM backtest_sensitivity
        WHERE frag_run_id = COALESCE(%(frag)s, (SELECT MAX(frag_run_id) FROM fragility_runs)) ORDER BY scenario, metric""",
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
    ap.add_argument("--cutoff", default=None, help="YYYY-MM-DD: only data through this day")
    ap.add_argument("--frag-run-id", type=int, default=None)
    args = ap.parse_args()
    prm = {"cutoff": args.cutoff, "frag": args.frag_run_id}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    counts = {}
    with get_conn() as conn:
        for name, sql in TABLES.items():
            cur = conn.execute(sql, prm)
            cols = [d.name for d in cur.description]
            rows = cur.fetchall()
            with open(out / name, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(cols)
                w.writerows([[cell(v) for v in r] for r in rows])
            counts[name] = (len(rows), cols)
            print(f"{name}: {len(rows)} rows")
        run = conn.execute("""SELECT frag_run_id, created_at, data_through, n_stock_days, seed, params, validation
                              FROM fragility_runs WHERE frag_run_id = COALESCE(%(frag)s, frag_run_id)
                              ORDER BY frag_run_id DESC LIMIT 1""", prm).fetchone()
        meta = conn.execute("""SELECT (SELECT MIN(price_date) FROM daily_prices),
                                      (SELECT MAX(price_date) FROM daily_prices
                                        WHERE %(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date),
                                      (SELECT MIN(sys_from) FROM price_versions)""", prm).fetchone()
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
        f"* Price history in the source database: {cell(meta[0])} to {cell(meta[1])}"
        + (f" (all files cut at {args.cutoff})." if args.cutoff else "."),
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
        "* Signals in `fragility_results.csv` (exact rules: docs/signals.md; the simulation is `src/fragility.py` `day_signals`): "
        "`trend` = UP if price > 50-day mean > 200-day mean, DOWN if price < 50-day mean < 200-day mean, else NEUTRAL "
        "(NEUTRAL with under 200 sessions of history); `is_anomaly` = one-day return beyond 3 standard deviations of the "
        "previous 20 returns, or volume z-score above 3; `breakout_52w` = price above the highest adjusted close of the "
        "previous 252 sessions; `strong_breakout` = breakout and volume z-score above 2; "
        "`rsi_zone` = +1 if the 14-day RSI (simple averages, not Wilder smoothing) is above 70, -1 if below 30, else 0 "
        "(exactly 30 or 70 is 0); `momentum_sign` = sign of price / price 20 sessions earlier - 1, computed unrounded "
        "(+1, -1, or 0 on an exact tie; days without 20 sessions of history are skipped); `sector_leader` = the sector "
        "(5 sectors, dashboard stocks) with the highest average 20-day momentum that day.",
        "* Fragility `flip_rate`: share of comparisons whose signal answer changed; `ci_low`/`ci_high`: 95% "
        "cluster-robust interval (cluster = contaminated day × simulation); `boot_low`/`boot_high`: "
        "95% moving-block bootstrap over trading days (20-day blocks), a robustness check.",
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

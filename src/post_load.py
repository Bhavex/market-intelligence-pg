"""Runs after every daily load:
1. refreshes the materialized reporting layer (mv_sector_daily)
2. runs data-quality checks and logs them in pipeline_runs

Exits with an error (turning the GitHub Action red) if any ERROR-level check fails.
Usage:  python src/post_load.py"""
import os
import sys

from db import get_conn, pipeline_run

# The final run of the day (next morning) sets STRICT_FRESHNESS=1: if any trading day is
# still missing then, the check fails and GitHub emails the repo owner.
STRICT = os.environ.get("STRICT_FRESHNESS") == "1"

# (name, severity, SQL returning the number of bad rows, what it means)
CHECKS = [
    ("freshness", "error" if STRICT else "warn",
     "SELECT COUNT(*) FROM v_data_freshness WHERE NOT is_fresh OR latest_price_date IS NULL",
     "Stocks missing the latest trading day (from the NSE holiday calendar)"),
    ("calendar_gaps_30d", "error" if STRICT else "warn",
     """SELECT COUNT(*)
        FROM generate_series(CURRENT_DATE - 30, expected_latest_trading_day(), INTERVAL '1 day') AS g(day)
        CROSS JOIN companies c
        WHERE c.is_active AND c.in_dashboard AND is_trading_day(g.day::date)
          AND NOT EXISTS (SELECT 1 FROM daily_prices p
                          WHERE p.ticker = c.ticker AND p.price_date = g.day::date)""",
     "Trading days in the last 30 (per NSE calendar) with no price row for a stock"),
    ("non_positive_prices", "error",
     "SELECT COUNT(*) FROM daily_prices WHERE close <= 0 OR open <= 0 OR high <= 0 OR low <= 0",
     "Prices that are zero or negative"),
    ("high_below_low", "error",
     "SELECT COUNT(*) FROM daily_prices WHERE high < low",
     "Rows where the day's high is below the low"),
    ("signals_coverage", "error",
     """SELECT COUNT(*) FROM daily_prices p
        LEFT JOIN signals s ON s.ticker = p.ticker AND s.signal_date = p.price_date
        WHERE s.ticker IS NULL""",
     "Price rows with no computed signal row"),
    ("missing_days_30d", "warn",
     """WITH days AS (SELECT DISTINCT price_date FROM daily_prices
                      WHERE price_date >= CURRENT_DATE - 30)
        SELECT COUNT(*) FROM days d CROSS JOIN companies c
        WHERE c.is_active AND c.in_dashboard
          AND NOT EXISTS (SELECT 1 FROM daily_prices p
                          WHERE p.ticker = c.ticker AND p.price_date = d.price_date)""",
     "Trading days (last 30) where a stock is missing but others have data"),
    ("close_outside_range", "warn",
     "SELECT COUNT(*) FROM daily_prices WHERE close > high * 1.001 OR close < low * 0.999",
     "Close price outside the day's high-low range"),
    ("extreme_moves_30d", "warn",
     "SELECT COUNT(*) FROM signals WHERE signal_date >= CURRENT_DATE - 30 AND ABS(return_1d) > 0.20",
     "Daily moves above 20% in the last 30 days (check for bad data or corporate actions)"),
    ("zero_volume_30d", "warn",
     "SELECT COUNT(*) FROM daily_prices WHERE price_date >= CURRENT_DATE - 30 AND volume = 0",
     "Zero-volume days in the last 30 days"),
]


def main():
    results = []
    with get_conn() as conn:
        with pipeline_run(conn, "data_quality") as run:
            conn.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY mv_sector_daily")
            conn.commit()
            print("Refreshed mv_sector_daily")
            expected = conn.execute("SELECT expected_latest_trading_day()").fetchone()[0]
            latest = conn.execute("SELECT MAX(price_date) FROM daily_prices").fetchone()[0]
            print(f"Expected latest trading day: {expected} | latest in database: {latest} | strict: {STRICT}")

            for name, severity, sql, meaning in CHECKS:
                bad = conn.execute(sql).fetchone()[0]
                status = "PASS" if bad == 0 else ("FAIL" if severity == "error" else "WARN")
                results.append((name, severity, bad, status, meaning))
                print(f"{status:4}  {name:22} {bad:>6}  {meaning}")
                if bad:
                    run["tickers_failed"].append(name)   # failed check names

            n_fail = sum(r[3] == "FAIL" for r in results)
            n_warn = sum(r[3] == "WARN" for r in results)
            run["rows_upserted"] = len(results)
            run["message"] = f"{len(results)} checks: {n_fail} failed, {n_warn} warnings"

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Data-quality checks\n\n| Check | Severity | Bad rows | Result |\n|---|---|---|---|\n")
            for name, severity, bad, status, _ in results:
                icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}[status]
                f.write(f"| {name} | {severity} | {bad} | {icon} {status} |\n")

    if any(r[3] == "FAIL" for r in results):
        sys.exit("Data-quality check failed")


if __name__ == "__main__":
    main()

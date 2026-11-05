"""Incremental daily load — run after market close (GitHub Actions does this on weekdays).

Every run re-fetches and upserts the last HEAL_SESSIONS (5) trading sessions for every ticker
(ON CONFLICT (ticker, price_date) DO UPDATE), so a wrong or incomplete row is overwritten by
Yahoo's final values on the next run without any manual edit. Today's bar is never stored
before FINAL_AFTER (16:30 IST, i.e. 15:30 close + buffer; see prices.py). The log reports how many
rows were inserted, updated, unchanged or held back. Signals are recomputed afterwards."""
import logging
from datetime import date

import fetch_config
import ledger
import observations
import quality
import run_lease
import timing
from db import active_tickers, get_conn, pipeline_run, record_settings
from prices import FetchError, _now, fetch_rows_ex, upsert_rows
from timing import stage
from validation import load_holidays, screen

DEFAULT_START = date(2015, 1, 1)
HEAL_SESSIONS = 5

# Start of the window = the 5th most recent NSE session up to the latest FINAL session
# (trading calendar in sql/07_trading_calendar.sql; today only counts after 16:30 IST).
HEAL_START_SQL = """
    SELECT d::date
    FROM generate_series(expected_latest_trading_day(COALESCE(%(at)s::timestamptz, now())) - 30,
                         expected_latest_trading_day(COALESCE(%(at)s::timestamptz, now())), INTERVAL '1 day') AS g(d)
    WHERE is_trading_day(d::date)
    ORDER BY d DESC
    OFFSET %(skip)s LIMIT 1
"""


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with get_conn() as conn:
        tickers = active_tickers(conn)
        holidays = load_holidays(conn)
        latest = dict(conn.execute(
            "SELECT ticker, MAX(price_date) FROM daily_prices GROUP BY ticker"
        ).fetchall())
        heal_start = conn.execute(HEAL_START_SQL, {"at": None, "skip": HEAL_SESSIONS - 1}).fetchone()[0]
        print(f"Re-upserting the last {HEAL_SESSIONS} trading sessions: from {heal_start}")

        with pipeline_run(conn, "daily_update") as run:
            fetch_config.check_version()
            settings_daily = fetch_config.register(conn, "1d")
            settings_hourly = fetch_config.register(conn, "1h")
            record_settings(conn, run["run_id"], settings_daily)
            earliest_start = _now().date()
            totals = dict(inserted=0, updated=0, unchanged=0, held_back=0, rejected=0)
            for t in tickers:
                # normally heal_start; further back if the stored data is older than that (a missed gap)
                start = min(heal_start, latest[t]) if t in latest else DEFAULT_START
                try:
                    with stage("fetch_yahoo", quiet=True):
                        f = fetch_rows_ex(t, start)
                    if not f.daily and not f.fallback and not f.held_back:
                        ledger.record_fetch(conn, f, None, run["run_id"], "daily_update", settings_daily, settings_hourly)
                        run["tickers_failed"].append(t)
                        print(f"{t}: FAILED (Yahoo returned no bars from {start})")
                        continue
                    with stage("validate_screen", quiet=True):
                        checked = screen(conn, t, f.daily, f.fallback, start, holidays, run["run_id"])
                    with stage("ledger_observations", quiet=True):
                        fetch_ids = ledger.record_fetch(conn, f, checked.result, run["run_id"], "daily_update", settings_daily, settings_hourly)
                        observations.record_fetched(conn, f, checked.result, fetch_ids)
                    totals["rejected"] += len(checked.result.rejected)
                    with stage("upsert_prices", quiet=True):
                        s = upsert_rows(conn, checked.daily, settings_id=settings_daily)
                        g = upsert_rows(conn, checked.fallback, insert_only=True, settings_id=settings_hourly)
                    with stage("quality_assess", quiet=True):
                        quality.assess_ticker(conn, t, checked.result, start, run["run_id"])
                    inserted, updated = s.inserted + g.inserted, s.updated
                    unchanged = s.unchanged + g.unchanged
                    for k, v in zip(totals, (inserted, updated, unchanged, f.held_back)):
                        totals[k] += v
                    run["rows_upserted"] += inserted + updated                 # rows actually written
                    if t in latest or inserted:   # a stock with no data at all must not force a full recompute
                        earliest_start = min(earliest_start, start)
                    note = f" (updated: {', '.join(map(str, s.updated_dates))})" if s.updated_dates else ""
                    held = f", held back {f.held_back} (session not final)" if f.held_back else ""
                    fb = f", hourly fallback offered for {len(checked.fallback)} gap day(s) (insert-only)" if checked.fallback else ""
                    bad = f", rejected {len(checked.result.rejected)} by validation" if checked.result.rejected else ""
                    print(f"{t}: inserted {inserted}, updated {updated}, unchanged {unchanged}{held}{fb}{bad} from {start}{note}")
                except Exception as exc:
                    conn.rollback()
                    if isinstance(exc, FetchError):
                        ledger.record_error(conn, exc.record, run["run_id"], "daily_update", settings_daily)
                    run["tickers_failed"].append(t)
                    print(f"{t}: FAILED ({exc})")

            run_lease.fence(conn)
            with stage("refresh_signals"):
                n_sig = conn.execute("SELECT refresh_signals(%s)", (earliest_start,)).fetchone()[0]
            conn.commit()
            max_date = conn.execute("SELECT MAX(price_date) FROM daily_prices").fetchone()[0]
            summary = (f"prices: {totals['inserted']} inserted, {totals['updated']} updated, "
                       f"{totals['unchanged']} unchanged, {totals['held_back']} held back (session not final), "
                       f"{totals['rejected']} rejected by validation")
            lineage = {"ledger": ledger.FAILURES[0], "observations": observations.FAILURES[0], "quality": quality.FAILURES[0]}
            lost = ", ".join(f"{k} {v}" for k, v in lineage.items() if v)
            run["message"] = (f"{summary}; {n_sig} signal rows refreshed; latest price date {max_date}"
                              + (f"; lineage write failures: {lost}" if lost else ""))
            print(summary)
            print(run["message"])
            timing.report("daily_update")

        dash = {r[0] for r in conn.execute("SELECT ticker FROM companies WHERE is_active AND in_dashboard")}
        dash_failed = [t for t in run["tickers_failed"] if t in dash]
        # the 25 dashboard stocks are judged on their own, so research stocks cannot hide their failures
        if len(run["tickers_failed"]) > len(tickers) // 2 or len(dash_failed) > len(dash) // 2:
            raise SystemExit(f"Too many failures: {run['tickers_failed']}")  # makes the Action go red


if __name__ == "__main__":
    main()

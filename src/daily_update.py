"""Incremental daily load — run after market close (GitHub Actions does this on weekdays).
For each ticker it re-fetches from 5 days before its latest stored date, so late
corrections from Yahoo are picked up, then recomputes recent signals."""
from datetime import date, timedelta

from db import active_tickers, get_conn, pipeline_run
from prices import fetch_rows, upsert_rows

DEFAULT_START = date(2015, 1, 1)
OVERLAP_DAYS = 5


def main():
    with get_conn() as conn:
        tickers = active_tickers(conn)
        latest = dict(conn.execute(
            "SELECT ticker, MAX(price_date) FROM daily_prices GROUP BY ticker"
        ).fetchall())

        with pipeline_run(conn, "daily_update") as run:
            earliest_start = date.today()
            for t in tickers:
                start = latest[t] - timedelta(days=OVERLAP_DAYS) if t in latest else DEFAULT_START
                earliest_start = min(earliest_start, start)
                try:
                    n = upsert_rows(conn, fetch_rows(t, start))
                    run["rows_upserted"] += n
                    print(f"{t}: {n} rows from {start}")
                except Exception as exc:
                    conn.rollback()
                    run["tickers_failed"].append(t)
                    print(f"{t}: FAILED ({exc})")

            n_sig = conn.execute("SELECT refresh_signals(%s)", (earliest_start,)).fetchone()[0]
            conn.commit()
            max_date = conn.execute("SELECT MAX(price_date) FROM daily_prices").fetchone()[0]
            run["message"] = f"{n_sig} signal rows refreshed; latest price date {max_date}"
            print(run["message"])

        if len(run["tickers_failed"]) > len(tickers) // 2:
            raise SystemExit(f"Too many failures: {run['tickers_failed']}")  # makes the Action go red


if __name__ == "__main__":
    main()

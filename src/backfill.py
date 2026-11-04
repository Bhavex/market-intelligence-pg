"""One-time historical load (default: 2015-01-01 to today), then computes signals.
Usage:  python src/backfill.py               # all active tickers
        python src/backfill.py INFY.NS TCS.NS --start 2020-01-01"""
import argparse
import logging
from datetime import date

from db import active_tickers, get_conn, pipeline_run
from prices import fetch_rows_ex, upsert_rows
from validation import load_holidays, screen


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="default: all active tickers")
    ap.add_argument("--start", default="2015-01-01")
    args = ap.parse_args()
    start = date.fromisoformat(args.start)

    with get_conn() as conn:
        tickers = args.tickers or active_tickers(conn)
        holidays = load_holidays(conn)
        with pipeline_run(conn, "backfill") as run:
            rejected = 0
            for i, t in enumerate(tickers, 1):
                try:
                    f = fetch_rows_ex(t, start)
                    checked = screen(conn, t, f.daily, f.fallback, start, holidays, run["run_id"])
                    rejected += len(checked.result.rejected)
                    s = upsert_rows(conn, checked.daily)
                    g = upsert_rows(conn, checked.fallback, insert_only=True)
                    n = s.total + g.total
                    run["rows_upserted"] += s.written + g.written
                    print(f"[{i}/{len(tickers)}] {t}: {n} rows ({s.written + g.written} written, "
                          f"{len(checked.fallback)} hourly fallback); validation: {checked.result.summary()}")
                    if n == 0:
                        run["tickers_failed"].append(t)
                except Exception as exc:
                    conn.rollback()
                    run["tickers_failed"].append(t)
                    print(f"[{i}/{len(tickers)}] {t}: FAILED ({exc})")

            print("Computing signals (this can take a minute) ...")
            n_sig = conn.execute("SELECT refresh_signals(%s)", (start,)).fetchone()[0]
            conn.commit()
            run["message"] = f"{len(tickers)} tickers, {n_sig} signal rows, {rejected} rows rejected by validation"
            print(f"Done: {run['rows_upserted']} price rows, {n_sig} signal rows, {rejected} rejected. "
                  f"Failed: {run['tickers_failed'] or 'none'}")


if __name__ == "__main__":
    main()

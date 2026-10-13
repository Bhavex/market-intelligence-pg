"""One-time historical load (default: 2015-01-01 to today), then computes signals.
Usage:  python src/backfill.py               # all active tickers
        python src/backfill.py INFY.NS TCS.NS --start 2020-01-01"""
import argparse
from datetime import date

from db import active_tickers, get_conn, pipeline_run
from prices import fetch_rows, upsert_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="default: all active tickers")
    ap.add_argument("--start", default="2015-01-01")
    args = ap.parse_args()
    start = date.fromisoformat(args.start)

    with get_conn() as conn:
        tickers = args.tickers or active_tickers(conn)
        with pipeline_run(conn, "backfill") as run:
            for i, t in enumerate(tickers, 1):
                try:
                    n = upsert_rows(conn, fetch_rows(t, start)).total
                    run["rows_upserted"] += n
                    print(f"[{i}/{len(tickers)}] {t}: {n} rows")
                    if n == 0:
                        run["tickers_failed"].append(t)
                except Exception as exc:
                    conn.rollback()
                    run["tickers_failed"].append(t)
                    print(f"[{i}/{len(tickers)}] {t}: FAILED ({exc})")

            print("Computing signals (this can take a minute) ...")
            n_sig = conn.execute("SELECT refresh_signals(%s)", (start,)).fetchone()[0]
            conn.commit()
            run["message"] = f"{len(tickers)} tickers, {n_sig} signal rows"
            print(f"Done: {run['rows_upserted']} price rows, {n_sig} signal rows. "
                  f"Failed: {run['tickers_failed'] or 'none'}")


if __name__ == "__main__":
    main()

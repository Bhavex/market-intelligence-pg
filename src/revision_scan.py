"""Weekly revision scan: re-download every stock's FULL history from Yahoo and upsert it.

The daily load only re-checks the last 5 sessions. Yahoo also changes OLD data: adjusted prices
are rescaled after every dividend, splits/bonuses rescale the whole history, and occasionally a
past close or volume is restated. Because daily_prices is versioned (sql/08_point_in_time.sql),
every value that differs from what we stored becomes a new version, stamped with this run's id and
classified in v_price_revisions. Unchanged rows are not rewritten.

Usage:  python src/revision_scan.py                 # all active tickers, from 2015-01-01
        python src/revision_scan.py INFY.NS --start 2024-01-01
"""
import argparse
from datetime import date

from db import active_tickers, get_conn, pipeline_run
from prices import fetch_rows_ex, upsert_rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tickers", nargs="*", help="default: all active tickers")
    ap.add_argument("--start", default="2015-01-01")
    args = ap.parse_args()
    start = date.fromisoformat(args.start)

    with get_conn() as conn:
        tickers = args.tickers or active_tickers(conn)
        with pipeline_run(conn, "revision_scan") as run:
            earliest, updated, inserted = None, 0, 0
            for i, t in enumerate(tickers, 1):
                try:
                    f = fetch_rows_ex(t, start)
                    s = upsert_rows(conn, f.daily)
                    g = upsert_rows(conn, f.fallback, insert_only=True)
                    updated += s.updated
                    inserted += s.inserted + g.inserted
                    if s.updated_dates:
                        first = min(s.updated_dates)
                        earliest = first if earliest is None else min(earliest, first)
                    if s.inserted + g.inserted:                 # a new stock or recovered days: recompute all
                        earliest = start
                    print(f"[{i}/{len(tickers)}] {t}: {s.total + g.total} rows checked, "
                          f"{s.updated} revised, {s.inserted + g.inserted} new")
                except Exception as exc:
                    conn.rollback()
                    run["tickers_failed"].append(t)
                    print(f"[{i}/{len(tickers)}] {t}: FAILED ({exc})")

            n_sig = 0
            if earliest is not None:
                print(f"Recomputing signals from {earliest} ...")
                n_sig = conn.execute("SELECT refresh_signals(%s)", (earliest,)).fetchone()[0]
                conn.commit()
            kinds = conn.execute("""
                SELECT revision_kind, COUNT(*) FROM v_price_revisions
                WHERE revised_by_run = %s GROUP BY 1 ORDER BY 2 DESC""", (run["run_id"],)).fetchall()
            run["rows_upserted"] = updated + inserted
            run["message"] = (f"{updated} rows revised, {inserted} new, {n_sig} signal rows recomputed; "
                              + (", ".join(f"{k} {n}" for k, n in kinds) or "no revisions"))
            print(run["message"])

        dash = {r[0] for r in conn.execute("SELECT ticker FROM companies WHERE is_active AND in_dashboard")}
        dash_failed = [t for t in run["tickers_failed"] if t in dash]
        if len(run["tickers_failed"]) > len(tickers) // 2 or len(dash_failed) > len(dash) // 2:
            raise SystemExit(f"Too many failures: {run['tickers_failed']}")


if __name__ == "__main__":
    main()

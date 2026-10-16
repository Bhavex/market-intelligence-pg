"""Second free feed: Yahoo's BSE listing (SYMBOL.BO) for every active stock, stored in
alt_feed_prices and checked against the official BSE file (v_feed_agreement, sql/11_research.sql).

Only finished sessions are stored (same 16:30 IST rule as the main feed). The first value received
for a day is kept in first_close; later changes update close/volume and count n_changes, so the
first-print accuracy of this feed can be measured too. Missing listings are skipped quietly and
the job never fails the pipeline.

Usage:  python src/alt_feed.py               # last 10 days
        python src/alt_feed.py --days 400    # about a year of history (first run)
"""
import argparse
import math
from datetime import timedelta

from db import active_tickers, get_conn, pipeline_run
from prices import _history, _now, _num, _session_finished

UPSERT = """
INSERT INTO alt_feed_prices (feed, ticker, price_date, close, adj_close, volume, first_close, run_id)
VALUES ('yahoo_bse', %(ticker)s, %(day)s, %(close)s, %(adj)s, %(volume)s, %(close)s, %(run_id)s)
ON CONFLICT (feed, ticker, price_date) DO UPDATE SET
    close = EXCLUDED.close, adj_close = EXCLUDED.adj_close, volume = EXCLUDED.volume,
    last_changed = now(), n_changes = alt_feed_prices.n_changes + 1, run_id = EXCLUDED.run_id
WHERE (alt_feed_prices.close, alt_feed_prices.volume) IS DISTINCT FROM (EXCLUDED.close, EXCLUDED.volume)
"""


def bse_symbol(ticker: str) -> str:
    return ticker[:-3] + ".BO" if ticker.endswith(".NS") else ticker


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    args = ap.parse_args()
    start = _now().date() - timedelta(days=args.days)

    with get_conn() as conn:
        tickers = active_tickers(conn)
        with pipeline_run(conn, "alt_feed") as run:
            stored, empty = 0, []
            for t in tickers:
                try:
                    df = _history(bse_symbol(t), 2, interval="1d", start=start.isoformat(),
                                  end=(_now().date() + timedelta(days=2)).isoformat())
                except Exception:
                    df = None
                rows = []
                for ts, r in (df.iterrows() if df is not None else []):
                    day, close = ts.date(), _num(r.get("Close"))
                    if close is None or close <= 0 or not _session_finished(day):
                        continue
                    vol = r.get("Volume")
                    vol = None if vol is None or (isinstance(vol, float) and math.isnan(vol)) else int(vol)
                    rows.append(dict(ticker=t, day=day, close=close, adj=_num(r.get("Adj Close")),
                                     volume=vol, run_id=run["run_id"]))
                if not rows:
                    empty.append(t)
                    continue
                with conn.cursor() as cur:
                    cur.executemany(UPSERT, rows)
                conn.commit()
                stored += len(rows)
            run["rows_upserted"] = stored
            run["message"] = (f"{len(tickers) - len(empty)} of {len(tickers)} stocks have a Yahoo BSE listing; "
                              f"{stored} rows checked" + (f"; none for {len(empty)}" if empty else ""))
            print(run["message"])


if __name__ == "__main__":
    main()

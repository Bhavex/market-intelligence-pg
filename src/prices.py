"""Fetch daily OHLCV from Yahoo Finance and upsert into daily_prices."""
import math
import time
from datetime import date, timedelta

import yfinance as yf

UPSERT_SQL = """
INSERT INTO daily_prices (ticker, price_date, open, high, low, close, adj_close, volume)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ticker, price_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, adj_close = EXCLUDED.adj_close,
    volume = EXCLUDED.volume, loaded_at = now()
"""


def _num(x):
    if x is None:
        return None
    x = float(x)
    return None if math.isnan(x) else round(x, 4)


def _history(ticker: str, retries: int = 3, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            return yf.Ticker(ticker).history(interval="1d", auto_adjust=False, actions=False, **kwargs)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 * attempt)


def _to_rows(ticker: str, df) -> dict:
    rows = {}
    for ts, r in df.iterrows():
        close = _num(r.get("Close"))
        if close is None or close <= 0:
            continue  # skip holidays / empty bars
        vol = r.get("Volume")
        vol = None if vol is None or (isinstance(vol, float) and math.isnan(vol)) else int(vol)
        rows[ts.date()] = (
            ticker, ts.date(),
            _num(r.get("Open")), _num(r.get("High")), _num(r.get("Low")),
            close, _num(r.get("Adj Close")), vol,
        )
    return rows


def fetch_rows(ticker: str, start: date, retries: int = 3) -> list[tuple]:
    """Download daily bars from `start` up to and including today.

    Two safeguards against a missing latest day:
    1. an explicit `end` two days ahead (Yahoo's end date is exclusive), so today's bar is
       always inside the requested range whatever the server's time zone is;
    2. a second request for the last 5 days (a different Yahoo code path), merged in,
       which picks up the newest bar when the date-range query lags behind."""
    end = date.today() + timedelta(days=2)
    rows = _to_rows(ticker, _history(ticker, retries, start=start.isoformat(), end=end.isoformat()))
    if (date.today() - start).days <= 31:          # daily runs only, not the 11-year backfill
        try:
            for d, row in _to_rows(ticker, _history(ticker, retries, period="5d")).items():
                rows.setdefault(d, row)
        except Exception:
            pass                                    # the main request already succeeded
    return [rows[d] for d in sorted(rows)]


def upsert_rows(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
    conn.commit()
    return len(rows)

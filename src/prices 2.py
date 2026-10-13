"""Fetch daily OHLCV from Yahoo Finance and upsert into daily_prices."""
import math
import time
from datetime import date, datetime, timedelta, timezone

import yfinance as yf

IST = timezone(timedelta(hours=5, minutes=30))

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
    """Returns (dataframe, metadata). Metadata holds Yahoo's live quote for the latest session."""
    for attempt in range(1, retries + 1):
        try:
            t = yf.Ticker(ticker)
            df = t.history(interval="1d", auto_adjust=False, actions=False, **kwargs)
            try:
                meta = t.history_metadata or {}
            except Exception:
                meta = {}
            return df, meta
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 * attempt)


def _session_close(meta, day):
    """Yahoo often leaves the newest daily candle's close empty for NSE stocks until its
    overnight batch runs, while the quote (regularMarketPrice) already has the closing price.
    Use that price, but only if the quote belongs to the same trading day."""
    try:
        price, ts = meta.get("regularMarketPrice"), meta.get("regularMarketTime")
        if not price or ts is None:
            return None
        if isinstance(ts, (int, float)):           # raw Unix seconds
            quote_day = datetime.fromtimestamp(ts, IST).date()
        else:                                      # newer yfinance: pandas Timestamp / datetime
            quote_day = ts.astimezone(IST).date()
        return _num(price) if quote_day == day else None
    except Exception:
        return None                                # never let the fallback break a normal load


def _to_rows(ticker: str, df, meta=None) -> dict:
    rows = {}
    last_day = df.index[-1].date() if len(df) else None
    for ts, r in df.iterrows():
        day = ts.date()
        close = _num(r.get("Close"))
        adj = _num(r.get("Adj Close"))
        if close is None and day == last_day and meta:
            close = _session_close(meta, day)      # fill the newest day from the live quote
            adj = adj or close                     # tomorrow's run overwrites with Yahoo's final values
        if close is None or close <= 0:
            continue                               # no usable price for this day
        vol = r.get("Volume")
        vol = None if vol is None or (isinstance(vol, float) and math.isnan(vol)) else int(vol)
        rows[day] = (
            ticker, day,
            _num(r.get("Open")), _num(r.get("High")), _num(r.get("Low")),
            close, adj, vol,
        )
    return rows


def fetch_rows(ticker: str, start: date, retries: int = 3) -> list[tuple]:
    """Download daily bars from `start` up to and including today.

    Safeguards against a missing latest day:
    1. an explicit `end` two days ahead (Yahoo's end date is exclusive);
    2. if Yahoo's newest candle has no close yet, the close is taken from the live quote
       for that same session (and replaced by the final value on the next run);
    3. a second request for the last 5 days, merged in, in case the date-range query lags."""
    end = date.today() + timedelta(days=2)
    df, meta = _history(ticker, retries, start=start.isoformat(), end=end.isoformat())
    rows = _to_rows(ticker, df, meta)
    if (date.today() - start).days <= 31:          # daily runs only, not the 11-year backfill
        try:
            df5, meta5 = _history(ticker, retries, period="5d")
            for d, row in _to_rows(ticker, df5, meta5).items():
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

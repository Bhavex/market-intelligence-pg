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
    for attempt in range(1, retries + 1):
        try:
            return yf.Ticker(ticker).history(auto_adjust=False, actions=False, **kwargs)
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 * attempt)


def _session_finished(day: date) -> bool:
    """A day's bar is final only after the NSE close (3:30 PM IST) plus a buffer."""
    now = datetime.now(IST)
    return day < now.date() or (day == now.date() and now.hour >= 16)


def _to_rows(ticker: str, df) -> dict:
    rows = {}
    for ts, r in df.iterrows():
        day = ts.date()
        close = _num(r.get("Close"))
        if close is None or close <= 0 or not _session_finished(day):
            continue                               # empty bar, or market still open
        vol = r.get("Volume")
        vol = None if vol is None or (isinstance(vol, float) and math.isnan(vol)) else int(vol)
        adj = _num(r.get("Adj Close")) or close
        rows[day] = (ticker, day, _num(r.get("Open")), _num(r.get("High")), _num(r.get("Low")),
                     close, adj, vol)
    return rows


def _rows_from_hourly(ticker: str, df) -> dict:
    """Rebuild daily bars from hourly bars: first open, max high, min low, last close, total volume.
    Yahoo sometimes leaves an NSE daily candle empty for days while its hourly bars are complete."""
    rows = {}
    df = df.dropna(subset=["Close"])
    for day, g in df.groupby(df.index.date):
        if not _session_finished(day) or g.empty:
            continue
        close = _num(g["Close"].iloc[-1])
        if close is None or close <= 0:
            continue
        rows[day] = (ticker, day, _num(g["Open"].iloc[0]), _num(g["High"].max()),
                     _num(g["Low"].min()), close, close, int(g["Volume"].fillna(0).sum()))
    return rows


def fetch_rows(ticker: str, start: date, retries: int = 3) -> list[tuple]:
    """Download daily bars from `start` up to the last finished session.

    Safeguards so no trading day is silently missed:
    1. an explicit `end` two days ahead (Yahoo's end date is exclusive);
    2. today's bar is skipped until the market has closed (no half-day prices);
    3. for recent days (last 30), any weekday Yahoo's daily data lacks or leaves empty is
       rebuilt from Yahoo's hourly bars (holidays have no hourly bars, so they stay empty)."""
    end = datetime.now(IST).date() + timedelta(days=2)
    df = _history(ticker, retries, interval="1d", start=start.isoformat(), end=end.isoformat())
    rows = _to_rows(ticker, df)

    recent_start = max(start, datetime.now(IST).date() - timedelta(days=30))
    wanted = {recent_start + timedelta(days=i) for i in range((datetime.now(IST).date() - recent_start).days + 1)}
    gaps = {d for d in wanted if d.weekday() < 5 and d not in rows and _session_finished(d)}
    if gaps:
        try:
            hourly = _history(ticker, retries, interval="1h", period="1mo")
            for d, row in _rows_from_hourly(ticker, hourly).items():
                if d in gaps:
                    rows[d] = row
        except Exception:
            pass                                    # daily data already loaded; strict check will flag gaps
    return [rows[d] for d in sorted(rows)]


def upsert_rows(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
    conn.commit()
    return len(rows)

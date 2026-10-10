"""Fetch daily OHLCV from Yahoo Finance and upsert into daily_prices."""
import math
import time
from datetime import date

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


def fetch_rows(ticker: str, start: date, retries: int = 3) -> list[tuple]:
    """Download daily bars from `start` to today. Returns rows ready for UPSERT_SQL."""
    for attempt in range(1, retries + 1):
        try:
            df = yf.Ticker(ticker).history(
                start=start.isoformat(), interval="1d", auto_adjust=False, actions=False
            )
            break
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2 * attempt)

    rows = []
    for ts, r in df.iterrows():
        close = _num(r.get("Close"))
        if close is None or close <= 0:
            continue  # skip holidays / empty bars
        vol = r.get("Volume")
        vol = None if vol is None or (isinstance(vol, float) and math.isnan(vol)) else int(vol)
        rows.append((
            ticker, ts.date(),
            _num(r.get("Open")), _num(r.get("High")), _num(r.get("Low")),
            close, _num(r.get("Adj Close")), vol,
        ))
    return rows


def upsert_rows(conn, rows: list[tuple]) -> int:
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
    conn.commit()
    return len(rows)

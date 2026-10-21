"""Fetch daily OHLCV from Yahoo Finance and upsert into daily_prices."""
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone

import yfinance as yf

import db

IST = timezone(timedelta(hours=5, minutes=30))

# NSE closes at 15:30 IST. A day's bar is stored only after close + a 1-hour buffer, so a
# partial intraday bar can never be saved. Keep in step with expected_latest_trading_day()
# in sql/07_trading_calendar.sql, which also uses 16:30.
FINAL_AFTER = dtime(16, 30)

_COLS = "(ticker, price_date, open, high, low, close, adj_close, volume, source)"
_VALUES = "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
# source is part of the comparison: an hourly-rebuilt row later confirmed by the daily candle is
# re-labelled (and versioned) even when the numbers are identical.
_PRICE_FIELDS = ("open", "high", "low", "close", "adj_close", "volume", "source")

# Insert a new row, or overwrite an existing one ONLY if a value actually differs. Unchanged rows
# are neither rewritten nor audited. xmax = 0 tells a fresh insert from an update.
UPSERT_SQL = f"""
INSERT INTO daily_prices {_COLS} {_VALUES}
ON CONFLICT (ticker, price_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, adj_close = EXCLUDED.adj_close,
    volume = EXCLUDED.volume, source = EXCLUDED.source, loaded_at = now()
WHERE ({", ".join("daily_prices." + c for c in _PRICE_FIELDS if c != "adj_close")})
      IS DISTINCT FROM ({", ".join("EXCLUDED." + c for c in _PRICE_FIELDS if c != "adj_close")})
   -- adj_close is recomputed by Yahoo on every download: ignore last-digit wobble (a real dividend
   -- adjustment moves it by 0.1 percent or more), so the weekly full-history scan does not create
   -- thousands of fake revisions in price_versions
   OR (daily_prices.adj_close IS NULL) <> (EXCLUDED.adj_close IS NULL)
   OR ABS(daily_prices.adj_close - EXCLUDED.adj_close) > GREATEST(0.00015, ABS(EXCLUDED.adj_close) * 0.000002)
RETURNING price_date, (xmax = 0) AS inserted
"""

# For approximate rows rebuilt from hourly bars: fill a gap, never overwrite an existing row.
INSERT_ONLY_SQL = f"""
INSERT INTO daily_prices {_COLS} {_VALUES}
ON CONFLICT (ticker, price_date) DO NOTHING
RETURNING price_date, TRUE AS inserted
"""


@dataclass
class UpsertStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0                 # already in the database with identical values
    updated_dates: tuple = ()

    @property
    def written(self) -> int:
        return self.inserted + self.updated

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged


class UnfinishedSessionError(ValueError):
    """Raised when rows for a session that has not closed yet (before FINAL_AFTER) reach the database."""


def guard_final_sessions(rows: list[tuple], intraday: bool = False, now: datetime | None = None) -> None:
    """Last line of defence: refuse to store a close for the current session before FINAL_AFTER
    (16:30 IST), or for a future date, unless the caller explicitly says these rows are intraday.
    The fetch functions already hold such bars back; this catches any other path into daily_prices."""
    if intraday:
        return
    bad = sorted({r[1] for r in rows if not _session_finished(r[1], now)})
    if bad:
        raise UnfinishedSessionError(
            f"refusing to store {len(bad)} unfinished session(s) as closes: {', '.join(map(str, bad))} "
            f"(sessions are final only from {FINAL_AFTER:%H:%M} IST; pass intraday=True to mark them as intraday)")


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


def _now() -> datetime:
    return datetime.now(IST)


def _session_finished(day: date, now: datetime | None = None) -> bool:
    """A day's bar is final only after the NSE close (15:30 IST) plus a buffer (FINAL_AFTER)."""
    now = (now or _now()).astimezone(IST)
    return day < now.date() or (day == now.date() and now.time() >= FINAL_AFTER)


def _to_rows(ticker: str, df) -> tuple[dict, int]:
    """Daily bars -> {date: row}, plus the number of valid bars held back because the session
    is not final yet (today before FINAL_AFTER)."""
    rows, held_back = {}, 0
    for ts, r in df.iterrows():
        day = ts.date()
        close = _num(r.get("Close"))
        if close is None or close <= 0:
            continue                               # empty bar
        if not _session_finished(day):
            held_back += 1                         # market still open or not settled: do not store
            continue
        vol = r.get("Volume")
        vol = None if vol is None or (isinstance(vol, float) and math.isnan(vol)) else int(vol)
        adj = _num(r.get("Adj Close")) or close
        rows[day] = (ticker, day, _num(r.get("Open")), _num(r.get("High")), _num(r.get("Low")),
                     close, adj, vol)
    return rows, held_back


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


@dataclass
class Fetched:
    daily: list              # rows from Yahoo's daily candles (authoritative)
    fallback: list           # rows rebuilt from hourly bars for weekdays Yahoo's daily data lacks (approximate)
    held_back: int = 0       # valid bars not stored because the session is not final yet


def fetch_rows_ex(ticker: str, start: date, retries: int = 3) -> Fetched:
    """Download daily bars from `start` up to the last finished session.

    Safeguards so no trading day is silently missed:
    1. an explicit `end` two days ahead (Yahoo's end date is exclusive);
    2. today's bar is held back until the market has closed (no half-day prices);
    3. for recent days (last 30), any weekday Yahoo's daily data lacks or leaves empty is
       rebuilt from Yahoo's hourly bars (holidays have no hourly bars, so they stay empty).
       These rows are approximate (first/last hourly price, summed hourly volume), so they are
       returned separately and must only fill gaps, never overwrite a stored row."""
    end = _now().date() + timedelta(days=2)
    df = _history(ticker, retries, interval="1d", start=start.isoformat(), end=end.isoformat())
    daily, held_back = _to_rows(ticker, df)

    recent_start = max(start, _now().date() - timedelta(days=30))
    wanted = {recent_start + timedelta(days=i) for i in range((_now().date() - recent_start).days + 1)}
    gaps = {d for d in wanted if d.weekday() < 5 and d not in daily and _session_finished(d)}
    fallback = {}
    if gaps:
        try:
            hourly = _history(ticker, retries, interval="1h", period="1mo")
            fallback = {d: row for d, row in _rows_from_hourly(ticker, hourly).items() if d in gaps}
        except Exception:
            pass                                    # daily data already loaded; strict check will flag gaps
    return Fetched([daily[d] for d in sorted(daily)], [fallback[d] for d in sorted(fallback)], held_back)


def fetch_rows(ticker: str, start: date, retries: int = 3) -> list[tuple]:
    """All rows (daily + hourly fallback) as one list; used by the one-time backfill."""
    f = fetch_rows_ex(ticker, start, retries)
    return sorted(f.daily + f.fallback, key=lambda r: r[1])


def upsert_rows(conn, rows: list[tuple], insert_only: bool = False, commit: bool = True,
                source: str | None = None, intraday: bool = False) -> UpsertStats:
    """Upsert rows and report how many were inserted, updated (value changed) or unchanged.
    insert_only=True never overwrites an existing row (used for approximate fallback rows).
    Rows are (ticker, date, open, high, low, close, adj_close, volume); the provenance label
    stored in daily_prices.source defaults to 'yahoo_hourly' for insert_only (hourly-rebuilt)
    rows and 'yahoo_daily' otherwise. Every change is versioned by 08_point_in_time.sql.
    Raises UnfinishedSessionError for a row of a session not final yet unless intraday=True;
    daily_prices has no intraday flag, so no caller sets it today."""
    stats = UpsertStats()
    if not rows:
        return stats
    guard_final_sessions(rows, intraday)
    src = source or ("yahoo_hourly" if insert_only else "yahoo_daily")
    run_id = db.current_run_id()
    if run_id is not None:   # provenance for the versioning trigger; transaction-local, so it is
        conn.execute("SELECT set_config('app.run_id', %s, true)", (str(run_id),))  # safe behind a pooler
    rows = [tuple(r) if len(r) == 9 else tuple(r) + (src,) for r in rows]
    updated_dates = []
    with conn.cursor() as cur:
        cur.executemany(INSERT_ONLY_SQL if insert_only else UPSERT_SQL, rows, returning=True)
        while True:
            res = cur.fetchone()
            if res is None:
                stats.unchanged += 1               # conflict, values identical (or insert_only and row exists)
            elif res[1]:
                stats.inserted += 1
            else:
                stats.updated += 1
                updated_dates.append(res[0])
            if not cur.nextset():
                break
    stats.updated_dates = tuple(updated_dates)
    if commit:
        conn.commit()
    return stats

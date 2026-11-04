import logging
import math

import fetch_config

FAILURES = [0]
log = logging.getLogger("market.observations")

APPEND_SQL = """
WITH v AS (
    SELECT %(fetch_id)s::bigint AS fetch_id, %(ticker)s::varchar(20) AS ticker, %(price_date)s::date AS price_date,
           %(source)s::varchar(24) AS source, %(open)s::numeric(14,4) AS open, %(high)s::numeric(14,4) AS high,
           %(low)s::numeric(14,4) AS low, %(close)s::numeric(14,4) AS close, %(adj_close)s::numeric(14,4) AS adj_close,
           %(volume)s::bigint AS volume, %(close_basis)s::varchar(32) AS close_basis,
           %(adj_close_basis)s::varchar(32) AS adj_close_basis, %(observed_at)s::timestamptz AS observed_at
)
INSERT INTO price_observations (fetch_id, ticker, price_date, source, open, high, low, close, adj_close, volume,
                                close_basis, adj_close_basis, observed_at)
SELECT v.fetch_id, v.ticker, v.price_date, v.source, v.open, v.high, v.low, v.close, v.adj_close, v.volume,
       v.close_basis, v.adj_close_basis, v.observed_at
FROM v
WHERE (SELECT o.value_hash FROM price_observations o
       WHERE o.ticker = v.ticker AND o.price_date = v.price_date AND o.source = v.source
       ORDER BY o.observation_id DESC LIMIT 1)
      IS DISTINCT FROM price_value_hash(v.open, v.high, v.low, v.close, v.adj_close, v.volume)
RETURNING observation_id, price_date
"""


def _num(x):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else round(x, 4)


def _vol(x):
    v = _num(x)
    return None if v is None else int(v)


def params(row, source, fetch_id, observed_at, close_basis=fetch_config.CLOSE_BASIS,
           adj_close_basis=fetch_config.ADJ_CLOSE_BASIS) -> dict:
    return {"fetch_id": fetch_id, "ticker": row[0], "price_date": row[1], "source": source,
            "open": _num(row[2]), "high": _num(row[3]), "low": _num(row[4]), "close": _num(row[5]),
            "adj_close": _num(row[6]), "volume": _vol(row[7]), "close_basis": close_basis,
            "adj_close_basis": adj_close_basis, "observed_at": observed_at}


def _exists(conn) -> bool:
    return conn.execute("SELECT to_regclass('public.price_observations')").fetchone()[0] is not None


def append(conn, items: list) -> dict:
    if not items:
        return {}
    try:
        with conn.transaction():
            if not _exists(conn):
                return {}
            out = {}
            for p in items:
                row = conn.execute(APPEND_SQL, p).fetchone()
                if row:
                    out[(p["ticker"], p["source"], row[1])] = row[0]
            return out
    except Exception as exc:
        FAILURES[0] += 1
        log.warning("could not append %d observation(s): %s", len(items), exc)
        return {}


def record_fetched(conn, fetched, result, fetch_ids: list) -> dict:
    ids = list(fetch_ids) + [None] * (len(fetched.requests) - len(fetch_ids))
    by_interval = {rec.interval: (fid, rec.retrieved_at) for rec, fid in zip(fetched.requests, ids)}
    items = []
    daily_fid, daily_at = by_interval.get("1d", (None, None))
    hourly_fid, hourly_at = by_interval.get("1h", (None, None))
    for row in fetched.daily:
        if daily_at is not None:
            items.append(params(row, "yahoo_daily", daily_fid, daily_at))
    for row in fetched.fallback:
        if hourly_at is not None:
            items.append(params(row, "yahoo_hourly", hourly_fid, hourly_at))
    return append(conn, items)

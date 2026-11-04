import argparse
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta

import fetch_config
import ledger
import observations
from db import active_tickers, get_conn, pipeline_run, record_settings
from prices import _now, _timed_history, _to_rows
from validation import validate_rows

log = logging.getLogger("market.revision_capture")

RECENT_SESSIONS = 10
FIELDS = ("open", "high", "low", "close", "adj_close", "volume")
ADJ_REL_TOL = 0.000002
ADJ_ABS_TOL = 0.00015

RECENT_START_SQL = """
    SELECT d::date
    FROM generate_series(expected_latest_trading_day() - 40, expected_latest_trading_day(), INTERVAL '1 day') AS g(d)
    WHERE is_trading_day(d::date)
    ORDER BY d DESC
    OFFSET %s LIMIT 1
"""

STORED_SQL = """
SELECT price_date, open::float8, high::float8, low::float8, close::float8, adj_close::float8, volume, loaded_at
FROM daily_prices WHERE ticker = %s AND price_date BETWEEN %s AND %s
"""

INSERT_SQL = """
INSERT INTO revision_captures (run_id, fetch_id, ticker, price_date, sample, kind, field, stored_value, fetched_value,
                               rel_diff, stored_loaded_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

COVERAGE_SQL = """
INSERT INTO revision_capture_coverage (run_id, ticker, sample, window_start, window_end, rows_compared, rows_differing, fetch_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (run_id, ticker, sample) DO NOTHING
"""


@dataclass(frozen=True)
class Difference:
    price_date: date
    kind: str
    field: str | None = None
    stored: float | None = None
    fetched: float | None = None
    rel_diff: float | None = None
    stored_loaded_at: object = None


def month_windows(first: date, before: date) -> list:
    out, cur = [], date(first.year, first.month, 1)
    while cur < before:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        out.append((max(cur, first), min(nxt - timedelta(days=1), before - timedelta(days=1))))
        cur = nxt
    return out


def rotating_windows(first: date, before: date, run_day: date, n: int = 1) -> list:
    windows = month_windows(first, before)
    if not windows or n <= 0:
        return []
    k = run_day.toordinal()
    step = max(1, len(windows) // max(n, 1))
    picks = sorted({(k + i * step) % len(windows) for i in range(n)})
    return [windows[i] for i in picks]


def _changed(field, stored, fetched) -> bool:
    if stored is None and fetched is None:
        return False
    if stored is None or fetched is None:
        return True
    if field == "adj_close":
        return abs(stored - fetched) > max(ADJ_ABS_TOL, abs(fetched) * ADJ_REL_TOL)
    return round(float(stored), 4) != round(float(fetched), 4)


def compare(stored: dict, fetched: dict, start: date, end: date) -> list:
    out = []
    for d in sorted(set(stored) | set(fetched)):
        if d < start or d > end:
            continue
        s, f = stored.get(d), fetched.get(d)
        if s is None:
            out.append(Difference(d, "new_in_feed"))
            continue
        if f is None:
            out.append(Difference(d, "missing_in_feed", stored_loaded_at=s[-1]))
            continue
        for i, field in enumerate(FIELDS):
            sv, fv = s[i], f[i]
            if _changed(field, sv, fv):
                rel = None if not sv or fv is None else float(fv) / float(sv) - 1
                out.append(Difference(d, "value_changed", field, sv, fv, rel, s[-1]))
    return out


def capture_window(conn, ticker: str, start: date, end: date, sample: str, run_id, settings_id) -> list:
    df, rec = _timed_history(ticker, 2, "1d", start=start.isoformat(), end=(end + timedelta(days=1)).isoformat())
    if rec.status == "error":
        ledger.write(conn, [ledger.yahoo_entry(rec, run_id, "revision_capture", settings_id)])
        raise RuntimeError(rec.error)
    rows, held = _to_rows(ticker, df) if df is not None else ({}, 0)
    rec.rows_parsed = len(rows)
    result = validate_rows(list(rows.values()))
    ids = ledger.write(conn, [ledger.yahoo_entry(rec, run_id, "revision_capture", settings_id, result, held)])
    fetch_id = ids[0] if ids else None
    observations.append(conn, [observations.params(r, "yahoo_daily", fetch_id, rec.retrieved_at) for r in rows.values()])
    stored = {r[0]: r[1:] for r in conn.execute(STORED_SQL, (ticker, start, end)).fetchall()}
    fetched = {d: r[2:8] for d, r in rows.items()}
    diffs = compare(stored, fetched, start, end) if rows else []
    with conn.transaction():
        with conn.cursor() as cur:
            cur.executemany(INSERT_SQL, [(run_id, fetch_id, ticker, x.price_date, sample, x.kind, x.field, x.stored,
                                          x.fetched, x.rel_diff, x.stored_loaded_at) for x in diffs])
            cur.execute(COVERAGE_SQL, (run_id, ticker, sample, start, end, len(set(stored) & set(fetched)),
                                       len({x.price_date for x in diffs}), fetch_id))
    conn.commit()
    return diffs


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Re-fetch recent and rotating older dates and record values that changed")
    ap.add_argument("--recent", type=int, default=RECENT_SESSIONS)
    ap.add_argument("--rotating-months", type=int, default=1)
    ap.add_argument("--tickers", nargs="*")
    args = ap.parse_args()
    with get_conn() as conn:
        tickers = args.tickers or active_tickers(conn)
        recent_start = conn.execute(RECENT_START_SQL, (args.recent - 1,)).fetchone()[0]
        recent_end = _now().date()
        first = conn.execute("SELECT MIN(price_date) FROM daily_prices").fetchone()[0] or recent_start
        older = rotating_windows(first, recent_start, _now().date(), args.rotating_months)
        with pipeline_run(conn, "revision_capture") as run:
            fetch_config.check_version()
            settings_id = fetch_config.register(conn, "1d")
            record_settings(conn, run["run_id"], settings_id)
            totals = {"recent": 0, "rotating": 0}
            for t in tickers:
                for start, end, sample in [(recent_start, recent_end, "recent")] + [(a, b, "rotating") for a, b in older]:
                    try:
                        diffs = capture_window(conn, t, start, end, sample, run["run_id"], settings_id)
                        totals[sample] += len(diffs)
                        if diffs:
                            print(f"{t} {sample} {start}..{end}: {len(diffs)} difference(s)")
                    except Exception as exc:
                        conn.rollback()
                        if t not in run["tickers_failed"]:
                            run["tickers_failed"].append(t)
                        print(f"{t} {sample}: FAILED ({exc})")
            windows = ", ".join(f"{a}..{b}" for a, b in older) or "none"
            run["rows_upserted"] = totals["recent"] + totals["rotating"]
            run["message"] = (f"recent {recent_start}..{recent_end}: {totals['recent']} difference(s); "
                              f"rotating {windows}: {totals['rotating']} difference(s); daily_prices not modified")
            print(run["message"])
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write(f"### Revision capture\n\n{run['message']}\n")
    if len(run["tickers_failed"]) > len(tickers) // 2:
        raise SystemExit(f"Too many failures: {run['tickers_failed']}")


if __name__ == "__main__":
    main()

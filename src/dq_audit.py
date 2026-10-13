"""Audit stored prices against Yahoo Finance for the last N trading sessions. Read-only.

Flags, per ticker and session:
  PRICE_DIFF         DB close differs from Yahoo's daily close by more than --tol percent   (error)
  LOADED_BEFORE_CLOSE row was last written on its own trading day before 16:30 IST (intraday bar) (error)
  NOT_TRADING_DAY    a row exists on a weekend or an NSE holiday (market_holidays)           (error)
  MISSING_DAY        an NSE trading day has no row                                           (error)
  STALE_CLOSE        close identical to the previous session's close (stale or duplicated)   (warn)
  NO_YAHOO_DAILY     Yahoo's daily series has no bar for a stored trading day: the row came from hourly
                     bars or an earlier load and cannot be checked against Yahoo daily       (warn)
  ADJ_DIFF / VOLUME_DIFF  adj_close >0.5% or volume >5% different from Yahoo                 (warn)

Exit code 1 if any error-level flag is raised.
Usage:  python src/dq_audit.py [--days 10] [--tol 0.5] [--detail MARUTI.NS --detail-days 15]"""
import argparse
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

from db import active_tickers, get_conn

IST = timezone(timedelta(hours=5, minutes=30))
ERROR_FLAGS = {"PRICE_DIFF", "LOADED_BEFORE_CLOSE", "NOT_TRADING_DAY", "MISSING_DAY"}
SESSIONS_SQL = """
    SELECT d::date FROM generate_series(expected_latest_trading_day() - 40, expected_latest_trading_day(), INTERVAL '1 day') AS g(d)
    WHERE is_trading_day(d::date) ORDER BY d DESC LIMIT %s"""


def yahoo_daily(ticker):
    df = yf.Ticker(ticker).history(period="2mo", interval="1d", auto_adjust=False, actions=False)
    return {ts.date(): r for ts, r in df.iterrows() if pd.notna(r.get("Close"))}


def pct(a, b):
    return (float(a) / float(b) - 1) * 100 if a is not None and b else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=10)
    ap.add_argument("--tol", type=float, default=0.5, help="close difference, in percent, above which to flag")
    ap.add_argument("--detail", default="MARUTI.NS")
    ap.add_argument("--detail-days", type=int, default=15)
    args = ap.parse_args()

    flags = defaultdict(list)                       # flag -> [(ticker, date, text)]
    detail = None
    with get_conn() as conn:
        sessions = sorted(r[0] for r in conn.execute(SESSIONS_SQL, (max(args.days, args.detail_days),)).fetchall())
        audit_sessions = sessions[-args.days:]
        first, last = min(sessions), max(sessions)
        holidays = {r[0] for r in conn.execute("SELECT holiday_date FROM market_holidays").fetchall()}
        print(f"Auditing {len(audit_sessions)} sessions {audit_sessions[0]} .. {audit_sessions[-1]} for all active tickers vs Yahoo daily "
              f"(NSE holiday table covers {min(holidays)}..{max(holidays)})")

        for t in active_tickers(conn):
            db = {r[0]: r for r in conn.execute(
                """SELECT price_date, open, high, low, close, adj_close, volume, loaded_at
                   FROM daily_prices WHERE ticker = %s AND price_date BETWEEN %s AND %s ORDER BY price_date""",
                (t, first - timedelta(days=10), last + timedelta(days=3))).fetchall()}
            yh = yahoo_daily(t)
            prev_close = None
            for d in sorted(db):                    # stale closes need the sessions before the audit window too
                if d.weekday() >= 5 or d in holidays:
                    if d >= audit_sessions[0]:
                        flags["NOT_TRADING_DAY"].append((t, d, "weekend" if d.weekday() >= 5 else "NSE holiday"))
                    continue
                close = float(db[d][4])
                if d >= audit_sessions[0] and prev_close is not None and close == prev_close:
                    flags["STALE_CLOSE"].append((t, d, f"close {close:g} equals the previous session's close"))
                prev_close = close
            for d in audit_sessions:
                if d not in db:
                    flags["MISSING_DAY"].append((t, d, "no row" + ("" if d in yh else " (Yahoo daily also has none)")))
                    continue
                _, o, h, l, c, adj, vol, loaded = db[d]
                loaded_ist = loaded.astimezone(IST)
                if loaded_ist.date() == d and loaded_ist.time() < datetime(2000, 1, 1, 16, 30).time():
                    flags["LOADED_BEFORE_CLOSE"].append((t, d, f"last written {loaded_ist:%H:%M} IST on the trading day itself"))
                y = yh.get(d)
                if y is None:
                    flags["NO_YAHOO_DAILY"].append((t, d, f"DB close {float(c):g}; Yahoo daily has no bar for this session"))
                    continue
                dc, da = pct(c, y["Close"]), pct(adj, y["Adj Close"])
                if abs(dc) > args.tol:
                    flags["PRICE_DIFF"].append((t, d, f"DB close {float(c):g} vs Yahoo {y['Close']:g} ({dc:+.2f}%)"))
                if da is not None and abs(da) > 0.5:
                    flags["ADJ_DIFF"].append((t, d, f"DB adj_close {float(adj):g} vs Yahoo {y['Adj Close']:g} ({da:+.2f}%)"))
                if vol and y["Volume"] and abs(vol / y["Volume"] - 1) > 0.05:
                    flags["VOLUME_DIFF"].append((t, d, f"DB volume {vol} vs Yahoo {int(y['Volume'])} ({pct(vol, y['Volume']):+.1f}%)"))
            if t == args.detail:
                rows = []
                for d in sessions[-args.detail_days:]:
                    r, y = db.get(d), yh.get(d)
                    rows.append({"date": d,
                                 "db_close": float(r[4]) if r else None, "yh_close": y["Close"] if y is not None else None,
                                 "db_adj": float(r[5]) if r else None, "yh_adj": y["Adj Close"] if y is not None else None,
                                 "db_vol": r[6] if r else None, "yh_vol": int(y["Volume"]) if y is not None else None,
                                 "db_loaded_ist": r[7].astimezone(IST).strftime("%m-%d %H:%M") if r else None})
                detail = pd.DataFrame(rows)

    if detail is not None:
        pd.set_option("display.width", 220)
        print(f"\n== {args.detail}: last {args.detail_days} sessions, DB vs Yahoo ==")
        print(detail.to_string(index=False))
    print("\n== Flags ==")
    order = ["PRICE_DIFF", "LOADED_BEFORE_CLOSE", "NOT_TRADING_DAY", "MISSING_DAY", "STALE_CLOSE", "NO_YAHOO_DAILY", "ADJ_DIFF", "VOLUME_DIFF"]
    for f in order:
        items = flags.get(f, [])
        print(f"{'ERROR' if f in ERROR_FLAGS else 'warn ':5} {f:20} {len(items):>4}")
        for t, d, text in items[:40]:
            print(f"        {t:14} {d}  {text}")
        if len(items) > 40:
            print(f"        ... {len(items) - 40} more")
    errors = sum(len(flags.get(f, [])) for f in ERROR_FLAGS)
    print(f"\n{'FAIL' if errors else 'PASS'}: {errors} error-level flag(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()

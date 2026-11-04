import argparse
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

import bhavcopy
import fetch_config
import ledger

log = logging.getLogger("market.reconcile")

COMPARED_SERIES = ("EQ", "BE")
SPLIT_FACTORS = (2, 3, 4, 5, 10, 1.5, 1.25, 4 / 3, 20)
CLASSES = ("match", "last_traded_not_close", "previous_session_close", "split_or_bonus_basis", "intraday_value",
           "unexplained", "missing_in_yahoo", "missing_in_bhavcopy")


@dataclass(frozen=True)
class Outcome:
    classification: str
    diff_bp: float | None
    detail: str = ""


def tolerance(close: float) -> float:
    return max(0.05, abs(close) * 0.0001)


def classify(yahoo_close, bhav) -> Outcome:
    if bhav is None:
        return Outcome("missing_in_bhavcopy", None)
    if yahoo_close is None or pd.isna(yahoo_close):
        return Outcome("missing_in_yahoo", None)
    y, c = float(yahoo_close), float(bhav["close"])
    bp = (y / c - 1) * 10000 if c else None
    tol = tolerance(c)
    if abs(y - c) <= tol:
        return Outcome("match", bp)
    if pd.notna(bhav.get("last")) and abs(y - float(bhav["last"])) <= tol:
        return Outcome("last_traded_not_close", bp, f"equals last traded price {bhav['last']}")
    if pd.notna(bhav.get("prev_close")) and abs(y - float(bhav["prev_close"])) <= tolerance(float(bhav["prev_close"])):
        return Outcome("previous_session_close", bp, f"equals previous close {bhav['prev_close']}")
    for k in SPLIT_FACTORS:
        if c and abs(y * k / c - 1) < 0.005:
            return Outcome("split_or_bonus_basis", bp, f"Yahoo close x {k:g} equals the unadjusted close")
    if pd.notna(bhav.get("low")) and pd.notna(bhav.get("high")) and float(bhav["low"]) - tol <= y <= float(bhav["high"]) + tol:
        return Outcome("intraday_value", bp, "inside the day's range but not the close or last price")
    return Outcome("unexplained", bp)


MAP_SQL = """
SELECT n.symbol AS nse_symbol, n.series, i.instrument_id, i.isin, y.symbol AS ticker
FROM instrument_symbols n
JOIN instruments i ON i.instrument_id = n.instrument_id
JOIN instrument_symbols y ON y.instrument_id = n.instrument_id AND y.vendor = 'yahoo'
 AND y.valid_from <= %(d)s AND (y.valid_to IS NULL OR y.valid_to > %(d)s)
WHERE n.vendor = 'nse' AND n.valid_from <= %(d)s AND (n.valid_to IS NULL OR n.valid_to > %(d)s)
"""

ISIN_SQL = """
SELECT i.isin, i.instrument_id, y.symbol AS ticker
FROM instruments i
JOIN instrument_symbols y ON y.instrument_id = i.instrument_id AND y.vendor = 'yahoo'
 AND y.valid_from <= %(d)s AND (y.valid_to IS NULL OR y.valid_to > %(d)s)
WHERE i.isin IS NOT NULL
"""

YAHOO_SQL = """
SELECT p.ticker, p.close::float8, p.volume, COALESCE(s.close_basis, 'split_adjusted') AS close_basis
FROM daily_prices p
LEFT JOIN fetch_settings s ON s.settings_id = p.settings_id
JOIN companies c ON c.ticker = p.ticker
WHERE p.price_date = %(d)s AND c.is_active
"""

INSERT_SQL = """
INSERT INTO reconciliation_results (run_id, fetch_id, trade_date, instrument_id, ticker, nse_symbol, series, isin,
                                    bhav_format, bhav_close, bhav_last, bhav_prev_close, bhav_low, bhav_high, bhav_volume,
                                    yahoo_close, yahoo_volume, yahoo_close_basis, bhav_close_basis, matched_by,
                                    classification, diff_bp, detail)
VALUES (%(run_id)s, %(fetch_id)s, %(trade_date)s, %(instrument_id)s, %(ticker)s, %(nse_symbol)s, %(series)s, %(isin)s,
        %(bhav_format)s, %(bhav_close)s, %(bhav_last)s, %(bhav_prev_close)s, %(bhav_low)s, %(bhav_high)s, %(bhav_volume)s,
        %(yahoo_close)s, %(yahoo_volume)s, %(yahoo_close_basis)s, %(bhav_close_basis)s, %(matched_by)s,
        %(classification)s, %(diff_bp)s, %(detail)s)
"""


def _f(x):
    return None if x is None or pd.isna(x) else float(x)


def reconcile_frame(conn, bhav: pd.DataFrame, day: date, run_id=None, fetch_id=None) -> list:
    bhav = bhav[bhav["trade_date"] == day]
    compared = bhav[bhav["series"].isin(COMPARED_SERIES)]
    by_symbol = {(r.symbol, r.series): r._asdict() for r in compared.itertuples(index=False)}
    by_isin = {}
    for r in compared.itertuples(index=False):
        if r.isin:
            by_isin.setdefault(r.isin, r._asdict())
    mapping = conn.execute(MAP_SQL, {"d": day}).fetchall()
    isin_map = {isin: (iid, t) for isin, iid, t in conn.execute(ISIN_SQL, {"d": day}).fetchall()}
    yahoo = {t: (c, v, b) for t, c, v, b in conn.execute(YAHOO_SQL, {"d": day}).fetchall()}
    results, seen_tickers = [], set()
    for nse_symbol, series, iid, isin, ticker in mapping:
        if ticker in seen_tickers:
            continue
        b, matched = None, None
        for s in COMPARED_SERIES:
            if (nse_symbol, s) in by_symbol:
                b, matched = by_symbol[(nse_symbol, s)], "symbol_series"
                break
        if b is None and isin and isin in by_isin:
            b, matched = by_isin[isin], "isin"
        y = yahoo.get(ticker)
        if b is None and y is None:
            continue
        seen_tickers.add(ticker)
        out = classify(None if y is None else y[0], b)
        results.append({
            "run_id": run_id, "fetch_id": fetch_id, "trade_date": day, "instrument_id": iid, "ticker": ticker,
            "nse_symbol": nse_symbol, "series": None if b is None else b["series"], "isin": isin or (b or {}).get("isin"),
            "bhav_format": None if b is None else b["format"], "bhav_close": _f((b or {}).get("close")),
            "bhav_last": _f((b or {}).get("last")), "bhav_prev_close": _f((b or {}).get("prev_close")),
            "bhav_low": _f((b or {}).get("low")), "bhav_high": _f((b or {}).get("high")),
            "bhav_volume": None if b is None or pd.isna(b["volume"]) else int(b["volume"]),
            "yahoo_close": None if y is None else y[0], "yahoo_volume": None if y is None else y[1],
            "yahoo_close_basis": None if y is None else y[2], "bhav_close_basis": fetch_config.OFFICIAL_CLOSE_BASIS,
            "matched_by": matched, "classification": out.classification,
            "diff_bp": None if out.diff_bp is None else round(out.diff_bp, 4), "detail": out.detail,
        })
    for isin, (iid, ticker) in isin_map.items():
        if ticker in seen_tickers or isin not in by_isin or ticker not in yahoo:
            continue
        b = by_isin[isin]
        y = yahoo[ticker]
        out = classify(y[0], b)
        seen_tickers.add(ticker)
        results.append({"run_id": run_id, "fetch_id": fetch_id, "trade_date": day, "instrument_id": iid, "ticker": ticker,
                        "nse_symbol": b["symbol"], "series": b["series"], "isin": isin, "bhav_format": b["format"],
                        "bhav_close": _f(b["close"]), "bhav_last": _f(b["last"]), "bhav_prev_close": _f(b["prev_close"]),
                        "bhav_low": _f(b["low"]), "bhav_high": _f(b["high"]),
                        "bhav_volume": None if pd.isna(b["volume"]) else int(b["volume"]),
                        "yahoo_close": y[0], "yahoo_volume": y[1], "yahoo_close_basis": y[2],
                        "bhav_close_basis": fetch_config.OFFICIAL_CLOSE_BASIS, "matched_by": "isin",
                        "classification": out.classification,
                        "diff_bp": None if out.diff_bp is None else round(out.diff_bp, 4), "detail": out.detail})
    if results:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.executemany(INSERT_SQL, results)
    return results


def bhav_ledger_entry(day, url, raw, status, fmt=None, rows=0, error=None, run_id=None, retrieved_at=None) -> dict:
    return {
        "run_id": run_id, "job_name": "reconcile", "source": "nse", "endpoint": f"bhavcopy:{fmt or bhavcopy.expected_format(day)}",
        "symbol": "ALL", "request": {"url": url, "trade_date": str(day)}, "retrieved_at": retrieved_at or datetime.now(timezone.utc),
        "response_sha256": bhavcopy.sha256(raw or b""), "hash_basis": "raw_bytes", "response_bytes": len(raw or b""),
        "rows_returned": rows, "rows_parsed": rows, "rows_valid": None, "rows_rejected": None, "rows_suspect": None,
        "rows_held_back": None, "library": "urllib", "library_version": None, "settings_id": None,
        "parser_version": bhavcopy.PARSER_VERSION, "validation_rule_version": None, "quality_rule_version": None,
        "status": status, "error": error,
    }


def reconcile_bytes(conn, raw: bytes, day: date | None = None, url: str = "file", run_id=None,
                    retrieved_at: datetime | None = None) -> list:
    try:
        frame = bhavcopy.parse(raw)
    except bhavcopy.BhavcopyFormatError as exc:
        ledger.write(conn, [bhav_ledger_entry(day or date.today(), url, raw, "parse_error", error=str(exc), run_id=run_id,
                                              retrieved_at=retrieved_at)])
        raise
    days = sorted(set(frame["trade_date"])) if day is None else [day]
    results = []
    for d in days:
        ids = ledger.write(conn, [bhav_ledger_entry(d, url, raw, "ok", frame["format"].iloc[0] if len(frame) else None,
                                                    int((frame["trade_date"] == d).sum()), run_id=run_id,
                                                    retrieved_at=retrieved_at)])
        results += reconcile_frame(conn, frame, d, run_id, ids[0] if ids else None)
    return results


def download(day: date, fetcher=None):
    from official_prices import Fetcher
    fetcher = fetcher or Fetcher("NSE")
    if not fetcher.warmed:
        try:
            fetcher._get("https://www.nseindia.com/", timeout=15)
        except Exception:
            pass
        fetcher.warmed = True
    tried = []
    for fmt in (bhavcopy.expected_format(day), bhavcopy.LEGACY if bhavcopy.expected_format(day) == bhavcopy.UDIFF else bhavcopy.UDIFF):
        u = bhavcopy.url(day, fmt)
        retrieved_at = datetime.now(timezone.utc)
        try:
            code, body = fetcher._get(u)
        except Exception as exc:
            tried.append((u, "blocked", None, retrieved_at, f"{type(exc).__name__}: {exc}"[:300]))
            continue
        if code == 200 and body:
            return u, body, retrieved_at, tried
        tried.append((u, "not_published" if code == 404 else "blocked" if code in (401, 403, 429) or code >= 500 else "error",
                      b"", retrieved_at, f"HTTP {code}"))
    return None, None, None, tried


def summarize(results: list) -> pd.DataFrame:
    if not results:
        return pd.DataFrame(columns=["classification", "n"])
    df = pd.DataFrame(results)
    return df.groupby("classification").size().reset_index(name="n").sort_values("n", ascending=False)


def main():
    from db import get_conn, pipeline_run
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(description="Reconcile NSE bhavcopy closes with stored Yahoo closes")
    ap.add_argument("--days", type=int, default=5, help="most recent NSE trading sessions to reconcile")
    ap.add_argument("--date", type=date.fromisoformat)
    ap.add_argument("--file", help="reconcile a bhavcopy file already on disk instead of downloading")
    args = ap.parse_args()
    all_results = []
    with get_conn() as conn:
        with pipeline_run(conn, "reconcile") as run:
            if args.file:
                with open(args.file, "rb") as fh:
                    all_results += reconcile_bytes(conn, fh.read(), args.date, args.file, run["run_id"])
            else:
                if args.date:
                    days = [args.date]
                else:
                    days = [r[0] for r in conn.execute(
                        """SELECT d::date FROM generate_series(expected_latest_trading_day() - 40,
                                  expected_latest_trading_day(), INTERVAL '1 day') AS g(d)
                           WHERE is_trading_day(d::date) ORDER BY d DESC LIMIT %s""", (args.days,)).fetchall()]
                for d in sorted(days):
                    u, raw, at, tried = download(d)
                    for tu, status, body, tat, err in tried:
                        ledger.write(conn, [bhav_ledger_entry(d, tu, body, status, error=err, run_id=run["run_id"],
                                                              retrieved_at=tat)])
                    if raw is None:
                        run["tickers_failed"].append(str(d))
                        print(f"{d}: no bhavcopy ({'; '.join(t[1] for t in tried)})")
                        continue
                    try:
                        res = reconcile_bytes(conn, raw, d, u, run["run_id"], at)
                        all_results += res
                        print(f"{d}: {len(res)} instruments reconciled")
                    except bhavcopy.BhavcopyFormatError as exc:
                        run["tickers_failed"].append(str(d))
                        print(f"{d}: unreadable bhavcopy ({exc})")
            table = summarize(all_results)
            run["rows_upserted"] = len(all_results)
            run["message"] = ", ".join(f"{r.classification} {r.n}" for r in table.itertuples()) or "nothing reconciled"
            print(run["message"])
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write("### Bhavcopy reconciliation\n\n| Classification | Instruments |\n|---|---|\n")
            for r in table.itertuples():
                fh.write(f"| {r.classification} | {r.n} |\n")


if __name__ == "__main__":
    main()

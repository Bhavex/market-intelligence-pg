"""Download the exchanges' official end-of-day files (bhavcopy) and store our 25 stocks in
reference_prices, so the free Yahoo feed can be checked against the official record
(see sql/09_reconciliation.sql).

Sources (UDiFF format, published by the exchanges for every session since July 2024):
  NSE  https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
  BSE  https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_YYYYMMDD_F_0000.CSV

Exchanges sometimes block automated downloads. Every attempt is logged in reference_fetch_log
(ok / not_published / blocked / error), a source that keeps refusing is skipped for the rest of
the run, and this script never fails the pipeline because of it.

Usage:  python src/official_prices.py                 # last 5 sessions not yet downloaded
        python src/official_prices.py --days 250      # backfill about a year
        python src/official_prices.py --date 2026-10-15 --force
        python src/official_prices.py --file NSE path/to/BhavCopy.csv.zip --date 2026-10-15
"""
import argparse
import io
import time
import urllib.error
import urllib.request
import zipfile
from datetime import date
from http.cookiejar import CookieJar

import pandas as pd

from db import get_conn, pipeline_run

UDIFF_START = date(2024, 7, 8)        # first session published in the UDiFF format
URLS = {
    "NSE": "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip",
    "BSE": "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d}_F_0000.CSV",
}
HOME = {"NSE": "https://www.nseindia.com/", "BSE": "https://www.bseindia.com/"}
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
MAX_BLOCKED_IN_A_ROW = 3


class Fetcher:
    """One cookie-keeping HTTP client per exchange (both sites set cookies on the home page)."""

    def __init__(self, source: str):
        self.source = source
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        self.warmed = False
        self.blocked_in_a_row = 0

    def _get(self, url: str, timeout: int = 25) -> tuple[int, bytes]:
        req = urllib.request.Request(url, headers={**HEADERS, "Referer": HOME[self.source]})
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, b""

    def fetch(self, day: date) -> tuple[str, int | None, bytes, str]:
        """Returns (status, http_status, csv_bytes, detail)."""
        if not self.warmed:
            try:
                self._get(HOME[self.source], timeout=15)
            except Exception:
                pass
            self.warmed = True
        url = URLS[self.source].format(d=day.strftime("%Y%m%d"))
        try:
            code, body = self._get(url)
        except Exception as exc:                       # timeout, DNS, connection reset ...
            return "blocked", None, b"", f"{type(exc).__name__}: {exc}"[:300]
        if code == 404:
            return "not_published", code, b"", url
        if code in (401, 403, 429) or code >= 500:
            return "blocked", code, b"", url
        if code != 200 or not body:
            return "error", code, b"", url
        try:
            if body[:2] == b"PK":                      # zip archive (NSE)
                with zipfile.ZipFile(io.BytesIO(body)) as z:
                    body = z.read(z.namelist()[0])
        except zipfile.BadZipFile as exc:
            return "error", code, b"", f"bad zip: {exc}"
        if b"TckrSymb" not in body[:2000]:             # an HTML error page served with 200
            return "blocked", code, b"", "response is not a bhavcopy CSV"
        return "ok", code, body, url


def parse(source: str, raw: bytes, companies: list[tuple[str, str | None]]) -> list[dict]:
    """Picks our stocks out of a UDiFF bhavcopy. companies = [(ticker, isin_or_None)]."""
    df = pd.read_csv(io.BytesIO(raw), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df = df.apply(lambda s: s.str.strip() if s.dtype == object else s)
    if "FinInstrmTp" in df:
        df = df[df["FinInstrmTp"].isin(["STK"]) | df["FinInstrmTp"].isna()]
    if source == "NSE" and "SctySrs" in df:
        df = df[df["SctySrs"] == "EQ"]
    num = lambda r, c: (float(r[c]) if c in r and pd.notna(r[c]) and r[c] != "" else None)
    out = []
    for ticker, isin in companies:
        sym = ticker.removesuffix(".NS").removesuffix(".BO")
        cand = df[df["ISIN"] == isin] if (isin and "ISIN" in df) else df.iloc[0:0]
        if cand.empty:
            cand = df[df["TckrSymb"] == sym]
        if cand.empty:
            continue
        if "TtlTradgVol" in cand:                       # several series/groups: keep the most traded
            cand = cand.assign(_v=pd.to_numeric(cand["TtlTradgVol"], errors="coerce")).sort_values("_v", ascending=False)
        r = cand.iloc[0]
        close = num(r, "ClsPric")
        if not close or close <= 0:
            continue
        vol = num(r, "TtlTradgVol")
        trades = num(r, "TtlNbOfTxsExctd")
        out.append(dict(ticker=ticker, open=num(r, "OpnPric"), high=num(r, "HghPric"), low=num(r, "LwPric"),
                        close=close, last_price=num(r, "LastPric"), prev_close=num(r, "PrvsClsgPric"),
                        volume=int(vol) if vol is not None else None,
                        n_trades=int(trades) if trades is not None else None,
                        isin=r["ISIN"] if "ISIN" in r and pd.notna(r["ISIN"]) else None))
    return out


UPSERT = """
INSERT INTO reference_prices (source, ticker, price_date, open, high, low, close, last_price,
                              prev_close, volume, n_trades, isin, run_id)
VALUES (%(source)s, %(ticker)s, %(day)s, %(open)s, %(high)s, %(low)s, %(close)s, %(last_price)s,
        %(prev_close)s, %(volume)s, %(n_trades)s, %(isin)s, %(run_id)s)
ON CONFLICT (source, ticker, price_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
    last_price = EXCLUDED.last_price, prev_close = EXCLUDED.prev_close, volume = EXCLUDED.volume,
    n_trades = EXCLUDED.n_trades, isin = EXCLUDED.isin, fetched_at = now(), run_id = EXCLUDED.run_id
"""

LOG = """
INSERT INTO reference_fetch_log (source, file_date, status, http_status, rows_matched, detail)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (source, file_date) DO UPDATE SET
    status = EXCLUDED.status, http_status = EXCLUDED.http_status, rows_matched = EXCLUDED.rows_matched,
    detail = EXCLUDED.detail, fetched_at = now()
"""


def store(conn, source: str, day: date, rows: list[dict], run_id) -> int:
    with conn.cursor() as cur:
        cur.executemany(UPSERT, [dict(r, source=source, day=day, run_id=run_id) for r in rows])
        if source == "NSE":                              # learn ISINs once, for exact BSE matching
            cur.executemany("UPDATE companies SET isin = %s WHERE ticker = %s AND isin IS NULL",
                            [(r["isin"], r["ticker"]) for r in rows if r["isin"]])
    return len(rows)


def target_days(conn, n_days: int, one_day: date | None) -> list[date]:
    if one_day:
        return [one_day]
    rows = conn.execute("""
        SELECT d::date FROM generate_series(GREATEST(%s::date, expected_latest_trading_day() - %s * 2),
                                            expected_latest_trading_day(), INTERVAL '1 day') AS g(d)
        WHERE is_trading_day(d::date)
        ORDER BY d DESC LIMIT %s""", (UDIFF_START, n_days, n_days)).fetchall()
    return sorted(r[0] for r in rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=5, help="most recent N sessions (default 5)")
    ap.add_argument("--date", type=date.fromisoformat, help="one specific session")
    ap.add_argument("--sources", default="NSE,BSE")
    ap.add_argument("--force", action="store_true", help="re-download files already stored")
    ap.add_argument("--file", nargs=2, metavar=("SOURCE", "PATH"),
                    help="load a bhavcopy downloaded by hand (needs --date)")
    args = ap.parse_args()
    sources = [s.strip().upper() for s in args.sources.split(",") if s.strip()]

    with get_conn() as conn:
        companies = conn.execute("SELECT ticker, isin FROM companies WHERE is_active ORDER BY ticker").fetchall()
        with pipeline_run(conn, "official_prices") as run:
            if args.file:
                src, path = args.file[0].upper(), args.file[1]
                raw = open(path, "rb").read()
                if raw[:2] == b"PK":
                    with zipfile.ZipFile(io.BytesIO(raw)) as z:
                        raw = z.read(z.namelist()[0])
                n = store(conn, src, args.date, parse(src, raw, companies), run["run_id"])
                conn.execute(LOG, (src, args.date, "ok", None, n, f"manual file {path}"))
                conn.commit()
                run["rows_upserted"] = n
                run["message"] = f"{src} {args.date}: {n} stocks from a manual file"
                print(run["message"])
                return

            # 'not_published' is only final for old dates: the exchanges post the file in the evening,
            # so a 404 at 17:10 IST must be retried by the next run.
            # A downloaded file is done unless stocks were added to the universe after it was read
            # (src/universe.py): then it is read again once, so the new stocks get their official history.
            done = {(s, d) for s, d in conn.execute(
                """SELECT source, file_date FROM reference_fetch_log
                   WHERE (status = 'ok' AND fetched_at >= (SELECT MAX(added_at) FROM companies WHERE is_active))
                      OR (status = 'not_published' AND file_date < CURRENT_DATE - 5)""").fetchall()}
            days = target_days(conn, args.days, args.date)
            fetchers = {s: Fetcher(s) for s in sources}
            counts = {s: {"ok": 0, "not_published": 0, "blocked": 0, "error": 0, "skipped": 0} for s in sources}
            for day in days:
                for s in sources:
                    f = fetchers[s]
                    if (s, day) in done and not args.force:
                        counts[s]["skipped"] += 1
                        continue
                    if f.blocked_in_a_row >= MAX_BLOCKED_IN_A_ROW:
                        counts[s]["skipped"] += 1
                        continue
                    status, code, raw, detail = f.fetch(day)
                    n = 0
                    if status == "ok":
                        try:
                            n = store(conn, s, day, parse(s, raw, companies), run["run_id"])
                            f.blocked_in_a_row = 0
                        except Exception as exc:
                            conn.rollback()
                            status, detail = "error", f"parse/store: {type(exc).__name__}: {exc}"[:300]
                    elif status == "blocked":
                        f.blocked_in_a_row += 1
                    conn.execute(LOG, (s, day, status, code, n, detail))
                    conn.commit()
                    counts[s][status] += 1
                    run["rows_upserted"] += n
                    print(f"{s} {day}: {status} (http {code}) {n} stocks")
                    time.sleep(1.0)                      # be polite to the exchanges' servers
            summary = "; ".join(f"{s}: " + ", ".join(f"{k} {v}" for k, v in c.items() if v) for s, c in counts.items())
            run["message"] = summary or "nothing to do"
            if all(c["ok"] == 0 and c["blocked"] > 0 for c in counts.values()):
                run["tickers_failed"].append("all_sources_blocked")    # status 'partial', visible in health
            print(run["message"])


if __name__ == "__main__":
    main()

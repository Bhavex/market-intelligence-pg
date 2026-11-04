import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg

from marketintel.sample import SampleConfig, generate_prices

FORBIDDEN_HOST_PARTS = ("neon.tech", "neon.build", "aws.neon")


def assert_throwaway(url: str) -> None:
    host = (urlparse(url).hostname or "") + " " + url
    if any(p in host.lower() for p in FORBIDDEN_HOST_PARTS):
        raise SystemExit("Refusing to load synthetic data into a Neon database. Use a throwaway local PostgreSQL.")


def synthetic_runs(end: date, n_days: int = 45):
    rows = []
    for i in range(n_days, 0, -1):
        d = end - timedelta(days=i - 1)
        if d.weekday() >= 5:
            continue
        start = datetime(d.year, d.month, d.day, 11, 40, tzinfo=timezone.utc)
        status, failed, msg = "success", [], "synthetic demo run: 25 tickers updated"
        if i == 17:
            status, failed, msg = "partial", ["DABUR.NS"], "synthetic demo run: Yahoo returned no bars for 1 ticker"
        if i == 9:
            status, failed, msg = "failed", [], "synthetic demo run: ConnectionError: Yahoo timeout"
        rows.append(("daily_update", start, start + timedelta(minutes=3, seconds=20 + i), status,
                     0 if status == "failed" else 25 * 5, failed, msg))
        rows.append(("data_quality", start + timedelta(minutes=4), start + timedelta(minutes=4, seconds=40),
                     "success", 10, [], "synthetic demo run: 10 checks: 0 failed, 1 warnings"))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Load synthetic prices into a throwaway PostgreSQL database")
    ap.add_argument("--years", type=float, default=10)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--end", type=date.fromisoformat, default=None)
    ap.add_argument("--replace", action="store_true", help="delete existing prices first")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL is not set")
    assert_throwaway(url)

    with psycopg.connect(url) as conn:
        n_existing = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
        if n_existing and not args.replace:
            print(f"daily_prices already has {n_existing} rows; nothing loaded (use --replace)")
            return
        if args.replace:
            conn.execute("TRUNCATE daily_prices, price_versions, rejected_prices, pipeline_runs CASCADE")
        end = args.end or conn.execute("SELECT expected_latest_trading_day()").fetchone()[0]
        start = end - timedelta(days=int(args.years * 365.25))
        holidays = [r[0] for r in conn.execute("SELECT holiday_date FROM market_holidays")]
        tickers = tuple(r[0] for r in conn.execute(
            "SELECT ticker FROM companies WHERE is_active AND in_dashboard ORDER BY ticker"))
        cfg = SampleConfig(start=start, end=end, seed=args.seed, tickers=tickers)
        df = generate_prices(cfg, holidays)
        cols = ["ticker", "price_date", "open", "high", "low", "close", "adj_close", "volume"]
        with conn.cursor() as cur:
            with cur.copy(f"COPY daily_prices ({', '.join(cols)}) FROM STDIN") as cp:
                for row in df[cols].itertuples(index=False):
                    cp.write_row(row)
        n_sig = conn.execute("SELECT refresh_signals(%s)", (start,)).fetchone()[0]
        with conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO pipeline_runs (job_name, started_at, finished_at, status, rows_upserted, tickers_failed, message)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""", synthetic_runs(end))
            cur.executemany(
                """INSERT INTO rejected_prices (ticker, price_date, check_name, detail, rejected_at)
                   VALUES (%s, %s, %s, %s, now() - %s * INTERVAL '1 day')""",
                [("LUPIN.NS", end - timedelta(days=3), "price_spike", "synthetic: +41.0% then -29.3% next session", 2),
                 ("ITC.NS", end - timedelta(days=12), "high_below_low", "synthetic: high 401.2 < low 405.0", 11),
                 ("TCS.NS", end - timedelta(days=12), "missing_value", "synthetic: missing open", 11)])
        conn.commit()
        conn.execute("REFRESH MATERIALIZED VIEW mv_sector_daily")
        conn.commit()
        print(f"Loaded {len(df)} synthetic price rows for {len(tickers)} tickers, {start} .. {end}; "
              f"{n_sig} signal rows")


if __name__ == "__main__":
    main()

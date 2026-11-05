import os
import signal
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import daily_update
import fetch_config
import prices

STAGE = sys.argv[1] if len(sys.argv) > 1 else "none"
TARGET = sys.argv[2] if len(sys.argv) > 2 else ""
DATES = [date.fromisoformat(d) for d in os.environ["CRASH_DATES"].split(",")]
TICKERS = os.environ["CRASH_TICKERS"].split(",")


def die():
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)


def bar(ticker, i):
    base = 100.0 + 10 * TICKERS.index(ticker) + i
    return (ticker, DATES[i], base, base + 1, base - 1, base + 0.5, base + 0.25, 1000 + i)


def fake_fetch(ticker, start, retries=3):
    if STAGE == "before_writes":
        die()
    if STAGE == "hold":
        flag = Path(os.environ["CRASH_HOLD_FILE"])
        flag.with_suffix(".started").write_text(ticker)
        while not flag.exists():
            time.sleep(0.05)
    rows = [bar(ticker, i) for i in range(len(DATES))]
    rec = prices.FetchRecord(ticker, "1d", {"start": start.isoformat()}, datetime.now(timezone.utc),
                             "0" * 64, len(rows), len(rows), "ok")
    return prices.Fetched(rows, [], 0, [rec])


real_upsert = prices.upsert_rows


def fake_upsert(conn, rows, insert_only=False, commit=True, source=None, intraday=False, settings_id=None):
    hit = bool(rows) and rows[0][0] == TARGET and not insert_only
    if hit and STAGE == "mid_transaction":
        real_upsert(conn, rows, insert_only, False, source, intraday, settings_id)
        die()
    out = real_upsert(conn, rows, insert_only, commit, source, intraday, settings_id)
    if hit and STAGE == "after_commit":
        die()
    return out


daily_update.fetch_rows_ex = fake_fetch
daily_update.upsert_rows = fake_upsert
fetch_config.check_version = lambda: True

if __name__ == "__main__":
    daily_update.main()

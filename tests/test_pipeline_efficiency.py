import time
from datetime import date

import pytest

import daily_update
from conftest import FakeTicker, bars
from test_insert import NOW, base_conn, pipeline_status

DAYS = ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
TICKERS = ["HDFCBANK.NS", "INFY.NS", "RELIANCE.NS", "TCS.NS", "WIPRO.NS"]


def build_conn(latest):
    conn = base_conn(TICKERS, previous=(date(2026, 10, 6), 100.0))
    conn.add_rule(r"SELECT ticker, MAX\(price_date\) FROM daily_prices GROUP BY ticker", list(latest.items()))
    conn.add_rule(r"OFFSET", [(date(2026, 10, 7),)])
    conn.add_rule(r"SELECT MAX\(price_date\) FROM daily_prices", [(date(2026, 10, 13),)])
    conn.add_rule(r"is_active AND in_dashboard", [(t,) for t in TICKERS])
    return conn


def run_pipeline(monkeypatch, fake_yf, frozen_now, capsys, workers):
    frozen_now(NOW)
    fake_yf["HDFCBANK.NS"] = FakeTicker(daily=bars(DAYS, [100, 101, 160, 102, 103]))
    fake_yf["INFY.NS"] = FakeTicker(error=RuntimeError("boom"))
    fake_yf["RELIANCE.NS"] = FakeTicker(daily=bars(DAYS, [50, 51, 52, 53, 54]))
    fake_yf["TCS.NS"] = FakeTicker(daily=bars(DAYS, [10, 10.1, 10.2, 10.3, 10.4]))
    fake_yf["WIPRO.NS"] = FakeTicker(daily=bars(DAYS[2:], [7, 7.1, 7.2]))
    conn = build_conn({t: date(2026, 10, 12) for t in TICKERS if t != "WIPRO.NS"})
    monkeypatch.setattr(daily_update, "FETCH_WORKERS", workers)
    monkeypatch.setattr(daily_update, "get_conn", lambda: conn)
    daily_update.main()
    out = [line for line in capsys.readouterr().out.splitlines() if not line.startswith("TIMING")]
    return out, sorted(conn.stored.items()), [(s, list(r)) for s, r in conn.executemany_calls], pipeline_status(conn)


def test_parallel_fetch_output_identical_to_serial(monkeypatch, fake_yf, frozen_now, capsys):
    serial = run_pipeline(monkeypatch, fake_yf, frozen_now, capsys, workers=1)
    parallel = run_pipeline(monkeypatch, fake_yf, frozen_now, capsys, workers=8)
    assert serial == parallel
    assert any("INFY.NS: FAILED" in line for line in serial[0])
    assert serial[3][0] == "partial"


def test_prefetch_keeps_order_and_exceptions():
    def fetch(t, start):
        time.sleep(0.02 * (5 - int(t)))
        if t == "2":
            raise ValueError("bad")
        return (t, start)

    jobs = [(str(i), date(2026, 1, i + 1)) for i in range(5)]

    def norm(results):
        return [(t, s, r, type(e).__name__ if e else None) for t, s, (r, e) in results]

    assert norm(daily_update.prefetch(jobs, fetch, workers=1)) == norm(daily_update.prefetch(jobs, fetch, workers=4))


class SignalConn:
    def __init__(self, missing):
        self.missing = missing
        self.calls = 0

    def execute(self, sql, params=None):
        self.calls += 1
        return self

    def fetchone(self):
        return (self.missing,)


@pytest.mark.parametrize("written,missing,expected,queried", [
    (3, False, True, 0),
    (0, True, True, 1),
    (0, False, False, 1),
])
def test_signal_refresh_skipped_only_when_nothing_changed(written, missing, expected, queried):
    conn = SignalConn(missing)
    assert daily_update.needs_signal_refresh(conn, written, date(2026, 10, 7)) is expected
    assert conn.calls == queried

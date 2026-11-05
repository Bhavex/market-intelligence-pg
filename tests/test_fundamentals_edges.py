from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import fundamentals


@pytest.mark.parametrize("raw,expected", [
    (1, 1.0), ("2.5", 2.5), (None, None), ("abc", None), (float("nan"), None),
    (float("inf"), None), (float("-inf"), None), ([], None),
])
def test_num(raw, expected):
    assert fundamentals._num(raw) == expected


class Conn:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.rows = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        if params and params[0] == self.fail_on:
            raise RuntimeError("write failed")
        self.rows.append(params)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def wire(monkeypatch, conn, infos):
    run = {"rows_upserted": 0, "tickers_failed": []}

    @contextmanager
    def fake_run(c, name):
        yield run

    def ticker(t):
        if isinstance(infos[t], Exception):
            raise infos[t]
        return SimpleNamespace(info=infos[t])

    monkeypatch.setattr(fundamentals, "get_conn", lambda: conn)
    monkeypatch.setattr(fundamentals, "active_tickers", lambda c: list(infos))
    monkeypatch.setattr(fundamentals, "pipeline_run", fake_run)
    monkeypatch.setattr(fundamentals.yf, "Ticker", ticker)
    monkeypatch.setattr(fundamentals.time, "sleep", lambda s: None)
    return run


def test_main_upserts_and_records_failures(monkeypatch):
    conn = Conn(fail_on="C.NS")
    infos = {
        "A.NS": {"marketCap": 10, "trailingPE": float("nan"), "dividendYield": "x"},
        "B.NS": RuntimeError("yahoo down"),
        "C.NS": {},
    }
    run = wire(monkeypatch, conn, infos)
    fundamentals.main()
    assert conn.rows == [("A.NS", 10.0, None, None, None, None, None)]
    assert run["rows_upserted"] == 1
    assert run["tickers_failed"] == ["B.NS", "C.NS"]
    assert conn.commits == 1 and conn.rollbacks == 2


def test_main_no_tickers(monkeypatch):
    conn = Conn()
    run = wire(monkeypatch, conn, {})
    fundamentals.main()
    assert run == {"rows_upserted": 0, "tickers_failed": []} and conn.rows == []

import re
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import prices  # noqa: E402


class FakeResult:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.description = [("col",)] if rows is not None else None

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)

    def __iter__(self):
        return iter(self.rows)


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._results = []
        self._current = FakeResult([])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._current = self.conn._run(sql, params)
        return self

    def executemany(self, sql, seq, returning=False):
        seq = list(seq)
        self.conn.executemany_calls.append((sql, seq))
        results = [self.conn._upsert(sql, r) for r in seq]
        if returning and results:
            self._results = results[1:]
            self._current = FakeResult([results[0]] if results[0] is not None else [])
        else:
            self._results = []
            self._current = FakeResult([])

    def fetchone(self):
        return self._current.fetchone()

    def fetchall(self):
        return self._current.fetchall()

    def nextset(self):
        if not self._results:
            return None
        nxt = self._results.pop(0)
        self._current = FakeResult([nxt] if nxt is not None else [])
        return True


class FakeConn:
    def __init__(self, rules=None, stored=None, tables=("rejected_prices",)):
        self.rules = list(rules or [])
        self.stored = dict(stored or {})
        self.tables = set(tables)
        self.executed = []
        self.executemany_calls = []
        self.commits = 0
        self.rollbacks = 0
        self.rejections = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def add_rule(self, pattern, rows):
        self.rules.insert(0, (pattern, rows))

    def _run(self, sql, params):
        self.executed.append((sql, params))
        flat = " ".join(sql.split())
        for pattern, rows in self.rules:
            if re.search(pattern, flat, re.I):
                return FakeResult(rows(params) if callable(rows) else rows)
        if "to_regclass" in flat:
            name = params[0] if params else re.search(r"'public\.(\w+)'", flat).group(1)
            return FakeResult([(name if name in self.tables else None,)])
        if flat.startswith("INSERT INTO pipeline_runs"):
            return FakeResult([(1,)])
        return FakeResult([])

    def _upsert(self, sql, row):
        flat = " ".join(sql.split())
        if flat.startswith("INSERT INTO rejected_prices"):
            self.rejections.append(row)
            return None
        key = (row[0], row[1])
        if key not in self.stored:
            self.stored[key] = tuple(row)
            return (row[1], True)
        if "DO NOTHING" in flat:
            return None
        if tuple(self.stored[key]) == tuple(row):
            return None
        self.stored[key] = tuple(row)
        return (row[1], False)

    def execute(self, sql, params=None):
        return self._run(sql, params)

    def cursor(self):
        return FakeCursor(self)

    @contextmanager
    def transaction(self):
        yield self

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def fake_conn():
    return FakeConn()


def bars(days, closes, volume=1000, spread=0.01, tz="Asia/Kolkata"):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days]).tz_localize(tz)
    c = np.asarray(closes, float)
    return pd.DataFrame({"Open": c, "High": c * (1 + spread), "Low": c * (1 - spread), "Close": c,
                         "Adj Close": c, "Volume": [volume] * len(c)}, index=idx)


def weekdays(start: date, n: int) -> list:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


class FakeTicker:
    def __init__(self, daily=None, hourly=None, error=None):
        self.daily = daily
        self.hourly = hourly
        self.error = error
        self.calls = []

    def history(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        if kwargs.get("interval") == "1h":
            return self.hourly if self.hourly is not None else pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        return self.daily if self.daily is not None else pd.DataFrame(columns=["Open", "High", "Low", "Close", "Adj Close", "Volume"])


@pytest.fixture
def frozen_now(monkeypatch):
    def set_now(dt: datetime):
        monkeypatch.setattr(prices, "_now", lambda: dt.astimezone(prices.IST))
    return set_now


@pytest.fixture
def fake_yf(monkeypatch):
    registry = {}

    def ticker(symbol):
        t = registry.get(symbol)
        if t is None:
            t = FakeTicker()
            registry[symbol] = t
        return t

    monkeypatch.setattr(prices.yf, "Ticker", ticker)
    monkeypatch.setattr(prices.time, "sleep", lambda s: None)
    return registry

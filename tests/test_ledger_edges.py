from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace

import fetch_config
import ledger


class Res:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Conn:
    def __init__(self, exists=True, rows=None, fail=False):
        self.exists = exists
        self.rows = list(rows or [])
        self.fail = fail
        self.calls = []

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "to_regclass" in sql:
            return Res(("fetch_log",) if self.exists else (None,))
        if self.fail:
            raise RuntimeError("boom")
        return Res(self.rows.pop(0) if self.rows else None)


def rec(interval="1d", status="ok"):
    return SimpleNamespace(interval=interval, symbol="X.NS", params={"start": "2024-01-01"},
                           retrieved_at=datetime(2024, 1, 1), response_sha256="h", rows_returned=3,
                           rows_parsed=3, status=status, error=None)


def test_write_empty_returns_empty_without_queries():
    c = Conn()
    assert ledger.write(c, []) == []
    assert c.calls == []


def test_write_missing_table_returns_empty():
    c = Conn(exists=False)
    assert ledger.write(c, [{"request": {}}]) == []
    assert len(c.calls) == 1


def test_write_returns_ids_and_none_for_missing_row():
    c = Conn(rows=[(7,), None])
    assert ledger.write(c, [{"request": {"a": 1}}, {}]) == [7, None]
    assert c.calls[1][1]["request"] == '{"a": 1}'
    assert c.calls[2][1]["request"] == "{}"


def test_write_failure_counts_and_returns_empty():
    before = ledger.FAILURES[0]
    assert ledger.write(Conn(fail=True), [{"request": {}}]) == []
    assert ledger.FAILURES[0] == before + 1


def test_yahoo_entry_without_result_has_null_counts():
    e = ledger.yahoo_entry(rec("1h"))
    assert e["rows_valid"] is None and e["rows_rejected"] is None and e["rows_suspect"] is None
    assert e["endpoint"] == "history:1h"
    assert e["request"]["interval"] == "1h" and e["request"]["start"] == "2024-01-01"
    assert e["source"] == fetch_config.SOURCE


def test_yahoo_entry_with_result_counts_rows():
    result = SimpleNamespace(valid=[1, 2], rejected=[3], suspect=[])
    e = ledger.yahoo_entry(rec(), run_id=1, job_name="j", settings_id=5, result=result, held_back=2)
    assert (e["rows_valid"], e["rows_rejected"], e["rows_suspect"]) == (2, 1, 0)
    assert e["rows_held_back"] == 2 and e["settings_id"] == 5


def test_record_fetch_routes_settings_and_result_by_interval():
    result = SimpleNamespace(valid=[1], rejected=[], suspect=[])
    fetched = SimpleNamespace(requests=[rec("1d"), rec("1h")], held_back=4)
    c = Conn(rows=[(1,), (2,)])
    assert ledger.record_fetch(c, fetched, result, settings_daily=10, settings_hourly=20) == [1, 2]
    first, second = c.calls[1][1], c.calls[2][1]
    assert first["settings_id"] == 10 and first["rows_held_back"] == 4 and first["rows_valid"] == 1
    assert second["settings_id"] == 20 and second["rows_held_back"] is None and second["rows_valid"] is None


def test_record_error_writes_single_entry():
    c = Conn(rows=[(9,)])
    assert ledger.record_error(c, rec(status="error"), run_id=3, job_name="j", settings_id=1) == [9]
    assert c.calls[1][1]["status"] == "error"

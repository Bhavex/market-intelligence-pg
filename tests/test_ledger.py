import os
from datetime import date, datetime

import pandas as pd
import pytest

import ledger
import prices
import validation as V
from conftest import FakeConn, FakeTicker, bars

TEST_DB = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 10, 13, 18, 0, tzinfo=prices.IST)


class TestHash:
    def test_same_frame_same_hash_and_any_change_changes_it(self):
        a = bars(["2026-10-09", "2026-10-12"], [100, 101])
        b = bars(["2026-10-09", "2026-10-12"], [100, 101])
        assert prices.frame_sha256(a) == prices.frame_sha256(b)
        b.iloc[1, b.columns.get_loc("Close")] = 101.0001
        assert prices.frame_sha256(a) != prices.frame_sha256(b)

    def test_empty_frame_has_the_empty_hash(self):
        assert prices.frame_sha256(None) == prices.frame_sha256(pd.DataFrame())


class TestFetchRecords:
    def test_daily_and_hourly_requests_are_recorded(self, fake_yf, frozen_now):
        frozen_now(NOW)
        hours = pd.DatetimeIndex(["2026-10-12 09:15", "2026-10-12 15:15"]).tz_localize("Asia/Kolkata")
        hourly = pd.DataFrame({"Open": [100.5, 101], "High": [102, 101.5], "Low": [100, 100.8],
                               "Close": [101, 101.2], "Volume": [10, 20]}, index=hours)
        fake_yf["INFY.NS"] = FakeTicker(daily=bars(["2026-10-09", "2026-10-13"], [100, 102]), hourly=hourly)
        f = prices.fetch_rows_ex("INFY.NS", date(2026, 10, 9))
        assert [r.interval for r in f.requests] == ["1d", "1h"]
        daily = f.requests[0]
        assert daily.rows_returned == 2 and daily.rows_parsed == 2 and daily.status == "ok"
        assert daily.params == {"start": "2026-10-09", "end": "2026-10-15"}
        assert f.requests[1].rows_parsed == 1

    def test_entry_carries_versions_and_counts(self, fake_yf, frozen_now):
        frozen_now(NOW)
        fake_yf["INFY.NS"] = FakeTicker(daily=bars(["2026-10-12", "2026-10-13"], [100, 160]))
        f = prices.fetch_rows_ex("INFY.NS", date(2026, 10, 12))
        res = V.validate_rows(f.daily)
        e = ledger.yahoo_entry(f.requests[0], run_id=5, job_name="daily_update", settings_id=9, result=res, held_back=0)
        assert e["parser_version"] == prices.PARSER_VERSION and e["validation_rule_version"] == V.RULE_VERSION
        assert e["library"] == "yfinance" and e["library_version"]
        assert (e["rows_valid"], e["rows_rejected"], e["rows_suspect"]) == (2, 0, 1)
        assert e["request"]["auto_adjust"] is False and e["hash_basis"] == "canonical_frame_csv"

    def test_write_is_a_noop_without_the_table(self):
        assert ledger.write(FakeConn(tables=()), [{"x": 1}]) == []

    def test_daily_update_records_one_entry_per_request(self, monkeypatch, fake_yf, frozen_now):
        import daily_update
        from test_insert import TestDailyUpdate
        frozen_now(NOW)
        fake_yf["INFY.NS"] = FakeTicker(daily=bars(["2026-10-12", "2026-10-13"], [100, 101]))
        fake_yf["TCS.NS"] = FakeTicker(error=RuntimeError("timeout"))
        conn = TestDailyUpdate().conn(["INFY.NS", "TCS.NS"], {"INFY.NS": date(2026, 10, 12), "TCS.NS": date(2026, 10, 12)})
        conn.tables.add("fetch_log")
        conn.add_rule("INSERT INTO fetch_log", [(1,)])
        monkeypatch.setattr(daily_update, "get_conn", lambda: conn)
        daily_update.main()
        logged = [p for sql, p in conn.executed if sql.strip().startswith("INSERT INTO fetch_log")]
        daily = {p["symbol"]: p["status"] for p in logged if p["endpoint"] == "history:1d"}
        hourly = {p["symbol"]: p["status"] for p in logged if p["endpoint"] == "history:1h"}
        assert daily == {"INFY.NS": "ok", "TCS.NS": "error"}
        assert hourly == {"INFY.NS": "empty"}


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
def test_ledger_rows_are_append_only_and_linked():
    import psycopg
    import fetch_config
    with psycopg.connect(TEST_DB) as conn:
        sid = fetch_config.register(conn, "1d")
        rec = prices.FetchRecord("INFY.NS", "1d", {"start": "2003-01-01"}, NOW, prices.frame_sha256(None), 0, 0, "empty")
        (fid,) = ledger.write(conn, [ledger.yahoo_entry(rec, settings_id=sid)])
        row = conn.execute("SELECT status, settings_id, parser_version, request->>'repair' FROM fetch_log WHERE fetch_id = %s",
                           (fid,)).fetchone()
        assert row == ("empty", sid, prices.PARSER_VERSION, "false")
        with pytest.raises(psycopg.errors.RestrictViolation):
            with conn.transaction():
                conn.execute("DELETE FROM fetch_log WHERE fetch_id = %s", (fid,))
        conn.rollback()

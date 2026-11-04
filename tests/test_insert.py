import sys
from datetime import date, datetime

import pandas as pd
import pytest

import backfill
import daily_update
import db
import prices
import validation as V
from conftest import FakeConn, FakeTicker, bars, weekdays

NOW = datetime(2026, 10, 13, 18, 0, tzinfo=prices.IST)
EARLY = datetime(2026, 10, 13, 9, 40, tzinfo=prices.IST)


def prow(day, close=100.0, ticker="INFY.NS", vol=1000):
    return (ticker, day, close, close * 1.01, close * 0.99, close, close, vol)


class TestUpsertRows:
    def test_counts_inserted_updated_unchanged(self, fake_conn, frozen_now):
        frozen_now(NOW)
        d1, d2 = date(2026, 10, 9), date(2026, 10, 12)
        s = prices.upsert_rows(fake_conn, [prow(d1), prow(d2)])
        assert (s.inserted, s.updated, s.unchanged) == (2, 0, 0)
        s = prices.upsert_rows(fake_conn, [prow(d1), prow(d2, 101)])
        assert (s.inserted, s.updated, s.unchanged) == (0, 1, 1)
        assert s.updated_dates == (d2,)
        assert s.written == 1 and s.total == 2

    def test_source_label_is_appended(self, fake_conn, frozen_now):
        frozen_now(NOW)
        prices.upsert_rows(fake_conn, [prow(date(2026, 10, 9))])
        prices.upsert_rows(fake_conn, [prow(date(2026, 10, 12))], insert_only=True)
        sql_daily, rows_daily = fake_conn.executemany_calls[0]
        sql_fb, rows_fb = fake_conn.executemany_calls[1]
        assert rows_daily[0][8] == "yahoo_daily" and "DO UPDATE" in sql_daily
        assert rows_fb[0][8] == "yahoo_hourly" and "DO NOTHING" in sql_fb

    def test_settings_id_is_stored_with_each_row(self, fake_conn, frozen_now):
        frozen_now(NOW)
        prices.upsert_rows(fake_conn, [prow(date(2026, 10, 9))], settings_id=42)
        sql, rows = fake_conn.executemany_calls[0]
        assert rows[0][9] == 42 and "settings_id" in sql

    def test_insert_only_never_overwrites(self, fake_conn, frozen_now):
        frozen_now(NOW)
        d = date(2026, 10, 9)
        prices.upsert_rows(fake_conn, [prow(d, 100)])
        s = prices.upsert_rows(fake_conn, [prow(d, 999)], insert_only=True)
        assert (s.inserted, s.updated, s.unchanged) == (0, 0, 1)
        assert fake_conn.stored[("INFY.NS", d)][5] == 100

    def test_commits_unless_told_not_to(self, fake_conn, frozen_now):
        frozen_now(NOW)
        prices.upsert_rows(fake_conn, [prow(date(2026, 10, 9))])
        assert fake_conn.commits == 1
        prices.upsert_rows(fake_conn, [prow(date(2026, 10, 12))], commit=False)
        assert fake_conn.commits == 1

    def test_empty_batch_touches_nothing(self, fake_conn):
        s = prices.upsert_rows(fake_conn, [])
        assert s.total == 0 and fake_conn.executed == [] and fake_conn.commits == 0

    def test_run_id_is_passed_to_versioning_trigger(self, fake_conn, frozen_now, monkeypatch):
        frozen_now(NOW)
        monkeypatch.setitem(db._CURRENT_RUN, "id", 77)
        prices.upsert_rows(fake_conn, [prow(date(2026, 10, 9))])
        assert any("set_config('app.run_id'" in sql and params == ("77",) for sql, params in fake_conn.executed)

    def test_unfinished_session_is_refused_before_any_sql(self, fake_conn, frozen_now):
        frozen_now(EARLY)
        with pytest.raises(prices.UnfinishedSessionError):
            prices.upsert_rows(fake_conn, [prow(date(2026, 10, 13))])
        assert fake_conn.executed == [] and fake_conn.executemany_calls == []


class TestFetch:
    def test_drops_empty_bars_and_holds_back_today(self, fake_yf, frozen_now):
        frozen_now(EARLY)
        df = bars(["2026-10-09", "2026-10-12", "2026-10-13"], [100, 101, 102])
        df.loc[df.index[0], "Close"] = float("nan")
        fake_yf["INFY.NS"] = FakeTicker(daily=df)
        f = prices.fetch_rows_ex("INFY.NS", date(2026, 10, 9))
        assert [r[1] for r in f.daily] == [date(2026, 10, 12)]
        assert f.held_back == 1

    def test_hourly_fallback_fills_a_missing_weekday(self, fake_yf, frozen_now):
        frozen_now(NOW)
        daily = bars(["2026-10-09", "2026-10-13"], [100, 102])
        hours = pd.DatetimeIndex(["2026-10-12 09:15", "2026-10-12 15:15"]).tz_localize("Asia/Kolkata")
        hourly = pd.DataFrame({"Open": [100.5, 101], "High": [102, 101.5], "Low": [100, 100.8],
                               "Close": [101, 101.2], "Volume": [10, 20]}, index=hours)
        fake_yf["INFY.NS"] = FakeTicker(daily=daily, hourly=hourly)
        f = prices.fetch_rows_ex("INFY.NS", date(2026, 10, 9))
        assert [r[1] for r in f.daily] == [date(2026, 10, 9), date(2026, 10, 13)]
        assert f.fallback == [("INFY.NS", date(2026, 10, 12), 100.5, 102.0, 100.0, 101.2, 101.2, 30)]

    def test_every_price_flag_is_explicit(self, fake_yf, frozen_now):
        frozen_now(NOW)
        t = FakeTicker(daily=bars(["2026-10-13"], [100]))
        fake_yf["INFY.NS"] = t
        prices.fetch_rows_ex("INFY.NS", date(2026, 10, 13))
        call = t.calls[0]
        for flag in ("auto_adjust", "back_adjust", "repair", "actions", "prepost", "keepna", "rounding"):
            assert call[flag] is False, flag

    def test_request_is_unadjusted_with_explicit_end(self, fake_yf, frozen_now):
        frozen_now(NOW)
        t = FakeTicker(daily=bars(["2026-10-13"], [100]))
        fake_yf["INFY.NS"] = t
        prices.fetch_rows_ex("INFY.NS", date(2026, 10, 13))
        call = t.calls[0]
        assert call["auto_adjust"] is False and call["interval"] == "1d"
        assert call["start"] == "2026-10-13" and call["end"] == "2026-10-15"

    def test_retries_then_raises(self, fake_yf, frozen_now):
        frozen_now(NOW)
        t = FakeTicker(error=ConnectionError("yahoo down"))
        fake_yf["INFY.NS"] = t
        with pytest.raises(ConnectionError):
            prices.fetch_rows_ex("INFY.NS", date(2026, 10, 1))
        assert len(t.calls) == 3


def pipeline_status(conn):
    upd = [p for sql, p in conn.executed if sql.strip().startswith("UPDATE pipeline_runs")]
    return upd[-1] if upd else None


def base_conn(tickers, previous=None):
    conn = FakeConn()
    conn.add_rule(r"SELECT ticker FROM companies WHERE is_active ORDER BY ticker", [(t,) for t in tickers])
    conn.add_rule(r"FROM market_holidays", [])
    conn.add_rule(r"price_date < %s ORDER BY price_date DESC LIMIT 1", lambda p: [previous] if previous else [])
    conn.add_rule(r"refresh_signals", [(5,)])
    return conn


class TestBackfill:
    def run(self, monkeypatch, conn, argv):
        monkeypatch.setattr(backfill, "get_conn", lambda: conn)
        monkeypatch.setattr(sys, "argv", ["backfill.py", *argv])
        backfill.main()

    def test_bad_rows_are_skipped_and_logged_not_fatal(self, monkeypatch, fake_yf, frozen_now):
        frozen_now(NOW)
        days = weekdays(date(2026, 9, 21), 10)
        closes = [100, 101, 102, 300, 103, 104, 105, 106, 107, 108]
        df = bars(days, closes)
        df.loc[df.index[6], ["High", "Low"]] = [90, 110]
        fake_yf["INFY.NS"] = FakeTicker(daily=df)
        conn = base_conn(["INFY.NS"])
        self.run(monkeypatch, conn, ["--start", "2026-09-21"])
        stored = sorted(d for _, d in conn.stored)
        assert days[3] in stored and days[6] not in stored
        assert len(stored) == 9
        assert [r[3] for r in conn.rejections] == [V.HIGH_BELOW_LOW]
        status = pipeline_status(conn)
        assert status[0] == "success" and "1 rows rejected" in status[3]

    def test_hourly_fallback_is_insert_only(self, monkeypatch, fake_yf, frozen_now):
        frozen_now(NOW)
        daily = bars(["2026-10-09", "2026-10-13"], [100, 102])
        hours = pd.DatetimeIndex(["2026-10-12 09:15", "2026-10-12 15:15"]).tz_localize("Asia/Kolkata")
        hourly = pd.DataFrame({"Open": [100.5, 101], "High": [102, 101.5], "Low": [100, 100.8],
                               "Close": [101, 101.2], "Volume": [10, 20]}, index=hours)
        fake_yf["INFY.NS"] = FakeTicker(daily=daily, hourly=hourly)
        conn = base_conn(["INFY.NS"])
        self.run(monkeypatch, conn, ["--start", "2026-10-09"])
        modes = [("DO NOTHING" in sql, [r[8] for r in rows]) for sql, rows in conn.executemany_calls
                 if "daily_prices" in sql]
        assert (False, ["yahoo_daily", "yahoo_daily"]) in modes
        assert (True, ["yahoo_hourly"]) in modes

    def test_one_failing_ticker_does_not_stop_the_others(self, monkeypatch, fake_yf, frozen_now):
        frozen_now(NOW)
        fake_yf["INFY.NS"] = FakeTicker(error=RuntimeError("boom"))
        fake_yf["TCS.NS"] = FakeTicker(daily=bars(["2026-10-12", "2026-10-13"], [10, 11]))
        conn = base_conn(["INFY.NS", "TCS.NS"])
        self.run(monkeypatch, conn, ["--start", "2026-10-12"])
        assert {k[0] for k in conn.stored} == {"TCS.NS"}
        status, rows, failed, _, _ = pipeline_status(conn)
        assert status == "partial" and failed == ["INFY.NS"] and rows == 2
        assert conn.rollbacks >= 1


class TestDailyUpdate:
    def run(self, monkeypatch, conn):
        monkeypatch.setattr(daily_update, "get_conn", lambda: conn)
        daily_update.main()

    def conn(self, tickers, latest):
        conn = base_conn(tickers, previous=(date(2026, 10, 6), 100.0))
        conn.add_rule(r"SELECT ticker, MAX\(price_date\) FROM daily_prices GROUP BY ticker", list(latest.items()))
        conn.add_rule(r"OFFSET", [(date(2026, 10, 7),)])
        conn.add_rule(r"SELECT MAX\(price_date\) FROM daily_prices", [(date(2026, 10, 13),)])
        conn.add_rule(r"is_active AND in_dashboard", [(t,) for t in tickers])
        return conn

    def test_heal_window_upserts_and_validates(self, monkeypatch, fake_yf, frozen_now):
        frozen_now(NOW)
        days = ["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
        fake_yf["INFY.NS"] = FakeTicker(daily=bars(days, [100, 101, 160, 102, 103]))
        fake_yf["TCS.NS"] = FakeTicker(daily=bars(days, [10, 10.1, 10.2, 10.3, 10.4]))
        conn = self.conn(["INFY.NS", "TCS.NS"], {"INFY.NS": date(2026, 10, 12), "TCS.NS": date(2026, 10, 12)})
        self.run(monkeypatch, conn)
        assert ("INFY.NS", date(2026, 10, 9)) in conn.stored
        assert len([k for k in conn.stored if k[0] == "TCS.NS"]) == 5
        status, written, failed, message, _ = pipeline_status(conn)
        assert status == "success" and failed == [] and written == 10
        assert "0 rejected by validation" in message

    def test_empty_yahoo_response_counts_as_failure(self, monkeypatch, fake_yf, frozen_now):
        frozen_now(NOW)
        fake_yf["INFY.NS"] = FakeTicker(daily=bars([], []))
        fake_yf["TCS.NS"] = FakeTicker(daily=bars(["2026-10-12", "2026-10-13"], [10, 10.1]))
        conn = self.conn(["INFY.NS", "TCS.NS"], {"INFY.NS": date(2026, 10, 12), "TCS.NS": date(2026, 10, 12)})
        self.run(monkeypatch, conn)
        status, _, failed, _, _ = pipeline_status(conn)
        assert status == "partial" and failed == ["INFY.NS"]

    def test_total_outage_turns_the_run_red(self, monkeypatch, fake_yf, frozen_now):
        frozen_now(NOW)
        fake_yf["INFY.NS"] = FakeTicker(daily=bars([], []))
        fake_yf["TCS.NS"] = FakeTicker(error=RuntimeError("timeout"))
        conn = self.conn(["INFY.NS", "TCS.NS"], {"INFY.NS": date(2026, 10, 12), "TCS.NS": date(2026, 10, 12)})
        with pytest.raises(SystemExit, match="Too many failures"):
            self.run(monkeypatch, conn)


class TestPipelineRun:
    def test_base_exception_marks_run_failed(self):
        conn = FakeConn()
        with pytest.raises(KeyboardInterrupt):
            with db.pipeline_run(conn, "job"):
                raise KeyboardInterrupt
        status, _, _, message, _ = pipeline_status(conn)
        assert status == "failed" and message.startswith("KeyboardInterrupt")
        assert db.current_run_id() is None

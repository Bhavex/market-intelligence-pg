import logging
import os
from datetime import date

import pytest

import validation as V
from conftest import FakeConn

HOLIDAYS_2026 = [(date(2026, 1, 26),), (date(2026, 3, 3),), (date(2026, 12, 25),)]


def holiday_conn():
    conn = FakeConn()
    conn.add_rule(r"SELECT holiday_date FROM market_holidays", HOLIDAYS_2026)
    return conn


def test_missing_year_warns_and_falls_back_to_weekends(caplog):
    with caplog.at_level(logging.WARNING, logger="market.validation"):
        holidays = V.load_holidays(holiday_conn(), today=date(2027, 1, 4))
    assert holidays == {r[0] for r in HOLIDAYS_2026}
    assert "NSE holiday list for 2027 is missing" in caplog.text
    assert "only weekends" in caplog.text
    gap = V.missing_sessions(date(2027, 1, 22), date(2027, 1, 28), holidays)
    assert gap == [date(2027, 1, 25), date(2027, 1, 26), date(2027, 1, 27)]


def test_covered_year_does_not_warn(caplog):
    with caplog.at_level(logging.WARNING, logger="market.validation"):
        V.load_holidays(holiday_conn(), today=date(2026, 6, 1))
    assert "missing" not in caplog.text


TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DB, reason="set TEST_DATABASE_URL to run database tests")
def test_sql_calendar_treats_uncovered_year_as_weekdays():
    import psycopg
    with psycopg.connect(TEST_DB) as conn:
        assert conn.execute("SELECT COUNT(*) FROM market_holidays WHERE holiday_date >= '2099-01-01'").fetchone()[0] == 0
        assert conn.execute("SELECT is_trading_day('2099-01-26')").fetchone()[0] is True
        assert conn.execute("SELECT is_trading_day('2099-01-24')").fetchone()[0] is False
        assert conn.execute(
            "SELECT expected_latest_trading_day('2099-01-26 20:00+05:30'::timestamptz)").fetchone()[0] == date(2099, 1, 26)

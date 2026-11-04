import os
from datetime import date, datetime, timedelta, timezone

import pytest

import fetch_config
import ledger
import observations as O
import prices
import validation as V
from conftest import FakeTicker, bars

TEST_DB = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 10, 13, 18, 0, tzinfo=prices.IST)


def test_params_carry_basis_and_clean_values():
    row = ("INFY.NS", date(2026, 10, 9), 1.123456, 2.0, 0.5, float("nan"), 1.5, 10.0)
    p = O.params(row, "yahoo_daily", 7, NOW)
    assert p["open"] == 1.1235 and p["close"] is None and p["volume"] == 10
    assert p["close_basis"] == fetch_config.CLOSE_BASIS and p["fetch_id"] == 7


def test_record_fetched_maps_each_row_to_its_request(fake_yf, frozen_now, monkeypatch):
    import pandas as pd
    frozen_now(NOW)
    hours = pd.DatetimeIndex(["2026-10-12 09:15", "2026-10-12 15:15"]).tz_localize("Asia/Kolkata")
    hourly = pd.DataFrame({"Open": [100.5, 101], "High": [102, 101.5], "Low": [100, 100.8],
                           "Close": [101, 101.2], "Volume": [10, 20]}, index=hours)
    fake_yf["INFY.NS"] = FakeTicker(daily=bars(["2026-10-09", "2026-10-13"], [100, 102]), hourly=hourly)
    f = prices.fetch_rows_ex("INFY.NS", date(2026, 10, 9))
    captured = []
    monkeypatch.setattr(O, "append", lambda conn, items: captured.extend(items) or {})
    O.record_fetched(None, f, None, [11, 12])
    assert [(i["source"], i["fetch_id"], i["price_date"]) for i in captured] == [
        ("yahoo_daily", 11, date(2026, 10, 9)), ("yahoo_daily", 11, date(2026, 10, 13)),
        ("yahoo_hourly", 12, date(2026, 10, 12))]


@pytest.fixture
def conn():
    import psycopg
    c = psycopg.connect(TEST_DB)
    c.execute("DELETE FROM daily_prices WHERE ticker = 'INFY.NS' AND price_date BETWEEN '2004-01-01' AND '2004-12-31'")
    yield c
    c.rollback()
    c.close()


def load(conn, rows, observed_at):
    rec = prices.FetchRecord("INFY.NS", "1d", {"start": "2004-01-01"}, observed_at, "0" * 64, len(rows), len(rows), "ok")
    f = prices.Fetched(rows, [], 0, [rec])
    res = V.validate_rows(rows)
    ids = ledger.record_fetch(conn, f, res, job_name="test")
    added = O.record_fetched(conn, f, res, ids)
    prices.upsert_rows(conn, res.valid, commit=False)
    return ids, added


def r(day, close):
    return ("INFY.NS", day, close, close * 1.01, close * 0.99, close, close, 1000)


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
class TestVersionedPrices:
    T1 = datetime(2004, 3, 2, 12, 0, tzinfo=timezone.utc)
    T2 = datetime(2004, 3, 5, 12, 0, tzinfo=timezone.utc)

    def test_revision_appends_and_as_of_returns_what_was_known(self, conn):
        d = date(2004, 3, 1)
        _, first = load(conn, [r(d, 100.0)], self.T1)
        _, second = load(conn, [r(d, 100.0)], self.T1 + timedelta(hours=1))
        _, third = load(conn, [r(d, 104.0)], self.T2)
        assert len(first) == 1 and second == {} and len(third) == 1
        obs = conn.execute("""SELECT close::float8 FROM price_observations WHERE ticker = 'INFY.NS' AND price_date = %s
                              ORDER BY observation_id""", (d,)).fetchall()
        assert [x[0] for x in obs] == [100.0, 104.0]
        then = conn.execute("SELECT close::float8 FROM observations_as_of(%s) WHERE ticker = 'INFY.NS' AND price_date = %s",
                            (self.T1 + timedelta(hours=2), d)).fetchone()[0]
        now = conn.execute("SELECT close::float8 FROM observations_as_of(now()) WHERE ticker = 'INFY.NS' AND price_date = %s",
                           (d,)).fetchone()[0]
        assert (then, now) == (100.0, 104.0)

    def test_selection_history_links_each_daily_prices_write_to_its_observation(self, conn):
        d = date(2004, 3, 2)
        load(conn, [r(d, 50.0)], self.T1)
        load(conn, [r(d, 51.0)], self.T2)
        sel = conn.execute("""SELECT s.action, o.close::float8, o.observed_at = %s
                              FROM price_selection s JOIN price_observations o USING (observation_id)
                              WHERE s.ticker = 'INFY.NS' AND s.price_date = %s ORDER BY s.selection_id""",
                           (self.T1, d)).fetchall()
        assert sel == [("insert", 50.0, True), ("update", 51.0, False)]
        prov = conn.execute("""SELECT has_observation, parser_version FROM v_selection_provenance
                               WHERE ticker = 'INFY.NS' AND price_date = %s""", (d,)).fetchone()
        assert prov == (True, prices.PARSER_VERSION)

    def test_selected_prices_as_of_and_deletion(self, conn):
        d = date(2004, 3, 3)
        load(conn, [r(d, 70.0)], self.T1)
        conn.execute("DELETE FROM daily_prices WHERE ticker = 'INFY.NS' AND price_date = %s", (d,))
        gone = conn.execute("SELECT COUNT(*) FROM selected_prices_as_of(now()) WHERE ticker = 'INFY.NS' AND price_date = %s",
                            (d,)).fetchone()[0]
        assert gone == 0
        assert conn.execute("SELECT COUNT(*) FROM price_observations WHERE ticker = 'INFY.NS' AND price_date = %s",
                            (d,)).fetchone()[0] == 1

    def test_writes_without_an_observation_are_flagged(self, conn):
        d = date(2004, 3, 4)
        conn.execute("INSERT INTO daily_prices (ticker, price_date, close) VALUES ('INFY.NS', %s, 10)", (d,))
        assert conn.execute("SELECT has_observation FROM v_selection_provenance WHERE ticker = 'INFY.NS' AND price_date = %s",
                            (d,)).fetchone()[0] is False

    def test_observations_and_selection_are_append_only(self, conn):
        import psycopg
        load(conn, [r(date(2004, 3, 8), 10.0)], self.T1)
        for sql in ("UPDATE price_observations SET close = 1 WHERE ticker = 'INFY.NS' AND price_date = '2004-03-08'",
                    "DELETE FROM price_observations WHERE ticker = 'INFY.NS' AND price_date = '2004-03-08'",
                    "UPDATE price_selection SET action = 'delete' WHERE ticker = 'INFY.NS' AND price_date = '2004-03-08'"):
            with pytest.raises(psycopg.errors.RestrictViolation):
                with conn.transaction():
                    conn.execute(sql)

    def test_existing_views_still_read_the_data(self, conn):
        d = date(2004, 3, 9)
        load(conn, [r(d, 80.0)], self.T1)
        conn.execute("SELECT refresh_signals('2004-03-01')")
        row = conn.execute("SELECT close::float8 FROM v_company_daily WHERE ticker = 'INFY.NS' AND price_date = %s",
                           (d,)).fetchone()
        assert row == (80.0,)

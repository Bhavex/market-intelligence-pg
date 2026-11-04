import os
from datetime import date

import pandas as pd
import pytest

from marketintel.datasource import COMPANY_DAILY_COLUMNS, DataUnavailable, DbSource, DemoSource, get_source

TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def demo():
    return DemoSource()


def test_get_source_defaults_to_demo(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert get_source().mode == "demo"
    assert get_source("postgresql://x@localhost/db").mode == "database"


def test_demo_has_25_companies_in_5_sectors(demo):
    assert len(demo.companies()) == 25
    assert demo.sectors()["n_companies"].tolist() == [5, 5, 5, 5, 5]


def test_demo_company_history_is_filtered_and_sorted(demo):
    lo, hi = demo.date_range()
    start = date(hi.year, 1, 1)
    h = demo.company_history("infy.ns", start, hi)
    assert set(COMPANY_DAILY_COLUMNS) <= set(h.columns)
    assert h["price_date"].is_monotonic_increasing
    assert h["price_date"].min() >= pd.Timestamp(start)
    assert (h["ticker"] == "INFY.NS").all()


def test_demo_pagination(demo):
    full = demo.company_history("TCS.NS")
    page = demo.company_page("TCS.NS", limit=10, offset=5, descending=True)
    assert page.total == len(full)
    assert page.rows["price_date"].tolist() == full["price_date"].iloc[::-1].iloc[5:15].tolist()


def test_demo_anomalies_are_flagged_rows(demo):
    a = demo.anomalies(limit=20)
    assert len(a) == 20 and a["is_anomaly"].all()
    assert a["price_date"].is_monotonic_decreasing


def test_demo_unknown_company(demo):
    assert demo.company("NOPE.NS") is None
    assert demo.company_history("NOPE.NS").empty


def test_missing_sample_file_raises(tmp_path):
    with pytest.raises(DataUnavailable):
        DemoSource(tmp_path).table("companies")


def test_db_source_reports_unavailable_database():
    src = DbSource("postgresql://nobody@127.0.0.1:1/none", connect_timeout=1)
    with pytest.raises(DataUnavailable):
        src.companies()


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL with sample data loaded")
def test_db_and_demo_agree_on_shape():
    db = DbSource(TEST_DB)
    if db.query("SELECT COUNT(*) AS n FROM daily_prices")["n"].iloc[0] == 0:
        pytest.skip("test database has no prices")
    demo = DemoSource()
    for name in ("companies", "sector_returns", "latest_by_sector", "freshness"):
        assert list(db.table(name).columns) == list(demo.table(name).columns), name
    t = db.companies()["ticker"].iloc[0]
    assert list(db.company_history(t).columns) == list(demo.company_history(t).columns)
    page = db.company_page(t, limit=5, offset=0, descending=True)
    assert len(page.rows) == 5 and page.total >= 5


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
def test_db_source_is_read_only():
    import psycopg
    db = DbSource(TEST_DB)
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        db.query("INSERT INTO market_holidays VALUES ('2099-01-01', 'x') RETURNING holiday_date")

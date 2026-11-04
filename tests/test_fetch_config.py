import os

import pytest

import fetch_config as F
from conftest import FakeConn

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def test_pinned_version_matches_requirements():
    req = open(os.path.join(os.path.dirname(__file__), "..", "requirements.txt")).read()
    assert f"yfinance=={F.PINNED_YFINANCE}" in req


def test_settings_describe_the_price_basis():
    s = F.settings("1d")
    assert s["params"]["auto_adjust"] is False and s["params"]["repair"] is False and s["params"]["actions"] is False
    assert s["close_basis"] == "split_adjusted" and s["adj_close_basis"] == "split_and_dividend_adjusted"
    assert s["library"] == "yfinance" and s["interval"] == "1d"


def test_hash_is_stable_and_sensitive():
    a, b = F.settings("1d"), F.settings("1d")
    assert F.settings_hash(a) == F.settings_hash(b)
    assert F.settings_hash(a) != F.settings_hash(F.settings("1h"))
    b["params"] = {**b["params"], "repair": True}
    assert F.settings_hash(a) != F.settings_hash(b)


def test_history_kwargs_are_complete():
    kw = F.history_kwargs("1h")
    assert kw["interval"] == "1h"
    assert {"auto_adjust", "back_adjust", "repair", "actions", "prepost", "keepna", "rounding"} <= set(kw)


def test_version_mismatch_is_reported(monkeypatch):
    monkeypatch.setattr(F, "library_version", lambda: "0.0.1")
    assert F.check_version() is False


def test_register_survives_a_missing_table():
    conn = FakeConn()
    conn.add_rule("INSERT INTO fetch_settings", lambda p: (_ for _ in ()).throw(RuntimeError("no table")))
    assert F.register(conn) is None


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
def test_register_is_idempotent_and_rows_carry_the_settings():
    import psycopg
    from datetime import date
    import prices
    with psycopg.connect(TEST_DB) as conn:
        a = F.register(conn, "1d")
        b = F.register(conn, "1d")
        h = F.register(conn, "1h")
        assert a == b and a != h
        row = ("INFY.NS", date(2002, 3, 4), 10.0, 11.0, 9.0, 10.5, 10.5, 100)
        prices.upsert_rows(conn, [row], settings_id=a, commit=False)
        got = conn.execute("""SELECT close_basis, adj_close_basis, library, params->>'auto_adjust'
                              FROM v_price_semantics WHERE ticker = 'INFY.NS' AND price_date = '2002-03-04'""").fetchone()
        assert got == ("split_adjusted", "split_and_dividend_adjusted", "yfinance", "false")
        conn.rollback()

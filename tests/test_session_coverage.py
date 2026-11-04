import os
from collections import Counter
from datetime import date, datetime, timedelta, timezone

import pytest

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
IST = timezone(timedelta(hours=5, minutes=30))
STATUSES = {"present", "holiday", "suspended", "not_yet_published", "fetch_failed", "missing"}


@pytest.fixture
def conn():
    import psycopg
    c = psycopg.connect(TEST_DB)
    yield c
    c.rollback()
    c.close()


def ist(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def instrument(conn, ticker):
    return conn.execute("""SELECT instrument_id FROM instrument_symbols
                           WHERE vendor = 'yahoo' AND symbol = %s ORDER BY valid_from DESC LIMIT 1""", (ticker,)).fetchone()[0]


def drop_row(conn, ticker, day):
    conn.execute("DELETE FROM daily_prices WHERE ticker = %s AND price_date = %s", (ticker, day))


def fetch(conn, ticker, at, status, start):
    conn.execute("""INSERT INTO fetch_log (source, endpoint, symbol, request, retrieved_at, response_sha256, hash_basis,
                                           parser_version, status)
                    VALUES ('yahoo', 'history:1d', %s, jsonb_build_object('start', %s::text), %s, %s,
                            'canonical_frame_csv', 'test', %s)""", (ticker, start.isoformat(), at, "0" * 64, status))


def coverage(conn, start, end, as_of, universe="test_cov"):
    rows = conn.execute("""SELECT ticker, session_date, status, detail
                           FROM session_coverage(%s, %s, %s, %s) ORDER BY ticker, session_date""",
                        (start, end, universe, as_of)).fetchall()
    return {(t, d): (s, why) for t, d, s, why in rows}


@pytest.fixture
def universe(conn):
    dab, lup = instrument(conn, "DABUR.NS"), instrument(conn, "LUPIN.NS")
    conn.execute("""INSERT INTO universe_membership (universe, instrument_id, valid_from, valid_to, reason, evidence)
                    VALUES ('test_cov', %s, '2026-10-01', NULL, 'fixture', 'test'),
                           ('test_cov', %s, '2026-10-01', '2026-10-15', 'fixture', 'test')""", (dab, lup))
    return dab, lup


def test_each_status_is_assigned_from_its_evidence(conn, universe):
    dab, lup = universe
    d = date
    for day in (d(2026, 10, 5), d(2026, 10, 6), d(2026, 10, 7), d(2026, 10, 8), d(2026, 10, 9), d(2026, 10, 12),
                d(2026, 10, 22), d(2026, 10, 23)):
        drop_row(conn, "DABUR.NS", day)
    fetch(conn, "DABUR.NS", ist(2026, 10, 8, 17, 10), "error", d(2026, 10, 8))
    conn.execute("""INSERT INTO trading_suspensions (instrument_id, valid_from, valid_to, reason, source)
                    VALUES (%s, '2026-10-06', '2026-10-07', 'surveillance suspension', 'fixture')""", (dab,))
    run = conn.execute("""INSERT INTO pipeline_runs (job_name, started_at, status, tickers_failed)
                          VALUES ('daily_update', %s, 'partial', ARRAY['DABUR.NS']) RETURNING run_id""",
                       (ist(2026, 10, 5, 17, 12),)).fetchone()[0]
    assert run
    fetch(conn, "DABUR.NS", ist(2026, 10, 9, 17, 10), "ok", d(2026, 10, 9))
    conn.execute("""INSERT INTO reconciliation_results (trade_date, instrument_id, ticker, bhav_close, classification)
                    VALUES ('2026-10-12', %s, 'LUPIN.NS', 4000, 'match')""", (lup,))
    got = coverage(conn, d(2026, 10, 1), d(2026, 10, 31), ist(2026, 10, 23, 6, 0))

    assert got[("DABUR.NS", d(2026, 10, 1))] == ("present", "yahoo_daily")
    assert got[("DABUR.NS", d(2026, 10, 2))] == ("holiday", "nse_calendar: Mahatma Gandhi Jayanti")
    assert got[("LUPIN.NS", d(2026, 10, 2))][0] == "holiday"
    assert got[("DABUR.NS", d(2026, 10, 5))] == ("fetch_failed", "listed in tickers_failed of a daily_update run after the close")
    assert got[("DABUR.NS", d(2026, 10, 6))] == ("suspended", "trading_suspensions: surveillance suspension")
    assert got[("DABUR.NS", d(2026, 10, 7))] == ("missing", "gap; no fetch recorded after the close")
    assert got[("DABUR.NS", d(2026, 10, 8))] == ("fetch_failed", "latest fetch after the close: error")
    assert got[("DABUR.NS", d(2026, 10, 9))] == ("missing", "gap; fetched after the close and no row stored")
    assert got[("DABUR.NS", d(2026, 10, 12))] == ("suspended", "inferred: absent from the NSE bhavcopy for this session")
    assert got[("DABUR.NS", d(2026, 10, 22))] == ("not_yet_published", "not fetched since the close yet")
    assert got[("DABUR.NS", d(2026, 10, 23))] == ("not_yet_published", "session not final yet")
    assert ("DABUR.NS", d(2026, 10, 24)) not in got and ("DABUR.NS", d(2026, 10, 26)) not in got
    assert {s for s, _ in got.values()} == STATUSES


def test_membership_dates_bound_the_expected_pairs(conn, universe):
    got = coverage(conn, date(2026, 9, 1), date(2026, 10, 31), ist(2026, 11, 1, 12))
    lup_days = sorted(day for t, day in got if t == "LUPIN.NS")
    assert lup_days[0] == date(2026, 10, 1) and lup_days[-1] == date(2026, 10, 14)
    assert all(day.weekday() < 5 for _, day in got)
    assert min(day for t, day in got if t == "DABUR.NS") == date(2026, 10, 1)


def test_a_late_fetch_success_turns_a_failure_back_into_a_gap(conn, universe):
    drop_row(conn, "DABUR.NS", date(2026, 10, 5))
    fetch(conn, "DABUR.NS", ist(2026, 10, 5, 17, 10), "error", date(2026, 10, 1))
    fetch(conn, "DABUR.NS", ist(2026, 10, 5, 20, 10), "ok", date(2026, 10, 1))
    got = coverage(conn, date(2026, 10, 5), date(2026, 10, 5), ist(2026, 10, 9, 12))
    assert got[("DABUR.NS", date(2026, 10, 5))][0] == "missing"
    early = coverage(conn, date(2026, 10, 5), date(2026, 10, 5), ist(2026, 10, 5, 18))
    assert early[("DABUR.NS", date(2026, 10, 5))][0] == "fetch_failed"


def test_holiday_is_inferred_only_when_the_calendar_does_not_cover_the_year(conn):
    day = conn.execute("""SELECT price_date FROM daily_prices WHERE price_date BETWEEN '2025-06-02' AND '2025-06-30'
                          ORDER BY price_date LIMIT 1 OFFSET 7""").fetchone()[0]
    conn.execute("DELETE FROM daily_prices WHERE price_date = %s", (day,))
    got = coverage(conn, day, day, ist(2026, 11, 1, 12), "dashboard_25")
    assert {s for s, _ in got.values()} == {"holiday"}
    assert all(why.startswith("inferred") for _, why in got.values())
    conn.execute("INSERT INTO calendar_coverage (year, source) VALUES (2025, 'fixture')")
    got = coverage(conn, day, day, ist(2026, 11, 1, 12), "dashboard_25")
    assert {s for s, _ in got.values()} == {"missing"}


def test_dated_membership_switches_tata_motors_to_hero(conn):
    switch = conn.execute("SELECT switch_date FROM v_tata_hero_switch").fetchone()[0]
    got = coverage(conn, switch - timedelta(days=7), switch + timedelta(days=7), ist(2026, 11, 1, 12), "dashboard_25")
    assert max(d for t, d in got if t == "TATAMOTORS.NS") < switch
    assert min(d for t, d in got if t == "HEROMOTOCO.NS") >= switch
    assert len({t for t, d in got if d == switch}) == 25


def test_summary_views_count_every_expected_pair_once(conn):
    total = conn.execute("SELECT COUNT(*) FROM v_expected_sessions").fetchone()[0]
    by_status = dict(conn.execute("SELECT status, pairs FROM v_session_coverage_summary WHERE year IS NULL").fetchall())
    assert set(by_status) <= STATUSES and sum(by_status.values()) == total
    per_year = conn.execute("SELECT SUM(pairs) FROM v_session_coverage_summary WHERE year IS NOT NULL").fetchone()[0]
    assert per_year == total
    tick = conn.execute("""SELECT SUM(present + missing + fetch_failed + holiday + suspended + not_yet_published)
                           FROM v_session_coverage_by_ticker""").fetchone()[0]
    assert tick == total
    dupes = conn.execute("""SELECT COUNT(*) FROM (SELECT ticker, session_date FROM v_session_coverage
                                                  GROUP BY 1, 2 HAVING COUNT(*) > 1) x""").fetchone()[0]
    assert dupes == 0
    assert Counter(r[0] for r in conn.execute("SELECT status FROM v_session_coverage")).keys() <= STATUSES

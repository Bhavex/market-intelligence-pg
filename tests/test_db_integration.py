import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

import fragility as F
import prices
import validation as V
from conftest import weekdays

TEST_DB = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_DB, reason="set TEST_DATABASE_URL to a throwaway database with the schema applied")

TICKERS = ("INFY.NS", "TCS.NS")
START = date(2001, 1, 1)


@pytest.fixture
def conn():
    import psycopg
    c = psycopg.connect(TEST_DB)
    c.execute("DELETE FROM daily_prices WHERE price_date < '2003-01-01' AND ticker = ANY(%s)", (list(TICKERS),))
    yield c
    c.rollback()
    c.close()


def load_walk(conn, n=320, seed=11):
    rng = np.random.default_rng(seed)
    days = weekdays(START, n)
    rows = []
    for t in TICKERS:
        px = 100 * np.cumprod(1 + rng.normal(0.0005, 0.015, n))
        vol = rng.integers(50_000, 150_000, n)
        vol[n - 5] = 2_000_000
        for d, p, v in zip(days, px, vol):
            p = round(float(p), 4)
            rows.append((t, d, p, round(p * 1.01, 4), round(p * 0.99, 4), p, p, int(v)))
    prices.upsert_rows(conn, rows, commit=False)
    conn.execute("SELECT refresh_signals(%s)", (START,))
    return days


def test_refresh_signals_matches_the_independent_pandas_engine(conn):
    load_walk(conn)
    cur = conn.execute("""SELECT p.ticker, p.price_date, 'IT' AS sector_name, TRUE AS in_dashboard, 'large' AS cap_group,
                                 p.open::float8, p.high::float8, p.low::float8, p.close::float8, p.adj_close::float8,
                                 p.volume::float8
                          FROM daily_prices p WHERE p.ticker = ANY(%s) AND p.price_date < '2003-01-01'
                          ORDER BY p.ticker, p.price_date""", (list(TICKERS),))
    cols = [d.name for d in cur.description]
    frame = pd.DataFrame(cur.fetchall(), columns=cols)
    cur = conn.execute("""SELECT ticker, signal_date AS price_date, trend AS trend_db, is_anomaly AS is_anomaly_db,
                                 breakout_52w AS breakout_52w_db, rsi_14::float8 AS rsi_14, momentum_20d::float8 AS momentum_20d
                          FROM signals WHERE ticker = ANY(%s) AND signal_date < '2003-01-01'""", (list(TICKERS),))
    stored = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    out = F.validate(F.features(frame), stored)
    assert out["rows_compared"] == len(frame) == 640
    assert out["trend_match_pct"] == 100
    assert out["anomaly_match_pct"] == 100
    assert out["breakout_rows"] > 0 and out["breakout_match_pct"] == 100
    assert out["rsi_within_0.01_pct"] == 100
    assert out["mom20_within_1e-6_pct"] == 100


def test_every_price_row_gets_a_signal_row(conn):
    days = load_walk(conn, n=60)
    n = conn.execute("""SELECT COUNT(*) FROM daily_prices p JOIN signals s
                        ON s.ticker = p.ticker AND s.signal_date = p.price_date
                        WHERE p.ticker = ANY(%s) AND p.price_date < '2003-01-01'""", (list(TICKERS),)).fetchone()[0]
    assert n == 2 * len(days)


def test_sma_200_is_null_until_200_sessions(conn):
    days = load_walk(conn, n=210)
    rows = conn.execute("""SELECT signal_date, sma_200 FROM signals WHERE ticker = 'INFY.NS'
                           AND signal_date < '2003-01-01' ORDER BY signal_date""").fetchall()
    assert rows[198][1] is None and rows[199][1] is not None
    assert rows[-1][0] == days[-1]


@pytest.mark.parametrize("sql", [
    "INSERT INTO daily_prices (ticker, price_date, open, high, low, close) VALUES ('INFY.NS', '2001-06-01', 0, 2, 1, 1.5)",
    "INSERT INTO daily_prices (ticker, price_date, open, high, low, close) VALUES ('INFY.NS', '2001-06-01', 1, 1, 2, 1.5)",
    "INSERT INTO daily_prices (ticker, price_date, open, high, low, close, adj_close) VALUES ('INFY.NS', '2001-06-01', 1, 2, 1, 1.5, -1)",
    "INSERT INTO daily_prices (ticker, price_date, close) VALUES ('INFY.NS', '1985-06-03', 1)",
    "INSERT INTO daily_prices (ticker, price_date, close) VALUES ('NOPE.NS', '2001-06-01', 1)",
])
def test_constraints_reject_impossible_prices(conn, sql):
    import psycopg
    with pytest.raises((psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation)):
        with conn.transaction():
            conn.execute(sql)


def test_signal_constraints(conn):
    import psycopg
    load_walk(conn, n=30)
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("UPDATE signals SET rsi_14 = 150 WHERE ticker = 'INFY.NS' AND signal_date < '2003-01-01'")
    with pytest.raises(psycopg.errors.CheckViolation):
        with conn.transaction():
            conn.execute("""UPDATE signals SET is_anomaly = TRUE, anomaly_reason = NULL
                            WHERE ticker = 'INFY.NS' AND signal_date < '2003-01-01'""")


def test_city_region_must_match_its_state(conn):
    import psycopg
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with conn.transaction():
            conn.execute("SET CONSTRAINTS cities_state_region_fk IMMEDIATE")
            conn.execute("""INSERT INTO cities (city_name, state, region, latitude, longitude)
                            VALUES ('Nagpur', 'Maharashtra', 'Central', 21.1458, 79.0882)""")


def test_all_constraints_are_validated_on_a_clean_database(conn):
    rows = conn.execute("""SELECT conname FROM pg_constraint
                           WHERE conname = ANY(%s) AND NOT convalidated""",
                        (["daily_prices_open_pos_chk", "signals_rsi_range_chk", "cities_state_region_fk",
                          "price_versions_ticker_fk"],)).fetchall()
    assert rows == []


def test_latest_prices_view_has_one_row_per_active_dashboard_stock(conn):
    load_walk(conn, n=30)
    conn.execute("UPDATE companies SET is_active = FALSE WHERE ticker = 'TCS.NS'")
    rows = conn.execute("SELECT ticker, latest_date, change_pct, prev_close FROM v_latest_prices_by_sector").fetchall()
    tickers = [r[0] for r in rows]
    assert len(tickers) == len(set(tickers))
    assert "TCS.NS" not in tickers
    n_active = conn.execute("""SELECT COUNT(*) FROM companies c WHERE is_active AND in_dashboard
                               AND EXISTS (SELECT 1 FROM daily_prices p WHERE p.ticker = c.ticker)""").fetchone()[0]
    assert len(rows) == n_active


def test_sector_performance_views(conn):
    load_walk(conn, n=80)
    rows = conn.execute("""SELECT price_date, n_stocks, advancers + decliners <= n_stocks, sector_index
                           FROM v_sector_daily_performance WHERE sector_name = 'IT' AND price_date < '2003-01-01'
                           ORDER BY price_date""").fetchall()
    assert rows[0][3] is not None and all(r[2] for r in rows)
    summary = conn.execute("SELECT sector_name, return_1d_pct, return_1m_pct, rank_1m FROM v_sector_returns_summary").fetchall()
    assert summary and len({r[0] for r in summary}) == len(summary)


def test_validation_rejections_are_stored(conn):
    res = V.validate_rows([("INFY.NS", date(2001, 6, 1), 10.0, 9.0, 11.0, 10.0, 10.0, 5)])
    assert V.record_rejections(conn, res.rejected, run_id=None) == 1
    row = conn.execute("""SELECT check_name, raw_row->>'high' FROM rejected_prices
                          WHERE ticker = 'INFY.NS' AND price_date = '2001-06-01'""").fetchone()
    assert row == (V.HIGH_BELOW_LOW, "9.0")


def test_screen_then_upsert_round_trip(conn):
    days = weekdays(START, 6)
    rows = [("INFY.NS", d, c, c * 1.01, c * 0.99, c, c, 100) for d, c in zip(days, [100, 101, 250, 102, 103, 104])]
    checked = V.screen(conn, "INFY.NS", rows, [], START)
    s = prices.upsert_rows(conn, checked.daily, commit=False)
    assert s.inserted == 5
    stored = [r[0] for r in conn.execute("""SELECT price_date FROM daily_prices WHERE ticker = 'INFY.NS'
                                            AND price_date < '2003-01-01' ORDER BY 1""").fetchall()]
    assert days[2] not in stored and len(stored) == 5

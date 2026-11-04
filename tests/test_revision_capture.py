import os
from datetime import date, datetime

import pytest

import prices
import revision_capture as R
from conftest import FakeTicker, bars

TEST_DB = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 10, 13, 18, 0, tzinfo=prices.IST)


def s(o, h, l, c, a, v, loaded="2026-10-10"):
    return (o, h, l, c, a, v, loaded)


class TestCompare:
    def test_unchanged_rows_produce_nothing(self):
        stored = {date(2026, 10, 9): s(1, 2, 0.5, 1.5, 1.5, 10)}
        fetched = {date(2026, 10, 9): (1, 2, 0.5, 1.5, 1.5, 10)}
        assert R.compare(stored, fetched, date(2026, 10, 1), date(2026, 10, 31)) == []

    def test_each_changed_field_is_reported_with_its_size(self):
        stored = {date(2026, 10, 9): s(1, 2, 0.5, 100.0, 99.0, 10)}
        fetched = {date(2026, 10, 9): (1, 2, 0.5, 101.0, 99.0, 12)}
        out = R.compare(stored, fetched, date(2026, 10, 1), date(2026, 10, 31))
        assert [(x.kind, x.field) for x in out] == [("value_changed", "close"), ("value_changed", "volume")]
        assert out[0].rel_diff == pytest.approx(0.01)

    def test_adj_close_wobble_is_ignored_but_a_dividend_rescale_is_not(self):
        stored = {date(2026, 10, 9): s(1, 2, 0.5, 100.0, 99.0, 10)}
        assert R.compare(stored, {date(2026, 10, 9): (1, 2, 0.5, 100.0, 99.0001, 10)},
                         date(2026, 10, 1), date(2026, 10, 31)) == []
        out = R.compare(stored, {date(2026, 10, 9): (1, 2, 0.5, 100.0, 98.5, 10)}, date(2026, 10, 1), date(2026, 10, 31))
        assert [x.field for x in out] == ["adj_close"]

    def test_missing_and_new_rows(self):
        stored = {date(2026, 10, 8): s(1, 2, 0.5, 1.5, 1.5, 10)}
        fetched = {date(2026, 10, 9): (1, 2, 0.5, 1.5, 1.5, 10)}
        kinds = {(x.price_date, x.kind) for x in R.compare(stored, fetched, date(2026, 10, 1), date(2026, 10, 31))}
        assert kinds == {(date(2026, 10, 8), "missing_in_feed"), (date(2026, 10, 9), "new_in_feed")}


class TestWindows:
    def test_month_windows_cover_the_range_without_gaps(self):
        w = R.month_windows(date(2015, 1, 5), date(2015, 4, 10))
        assert w[0] == (date(2015, 1, 5), date(2015, 1, 31)) and w[-1] == (date(2015, 4, 1), date(2015, 4, 9))
        assert all(b < c for (_, b), (c, _) in zip(w, w[1:]))

    def test_rotation_is_deterministic_and_moves_every_day(self):
        a = R.rotating_windows(date(2015, 1, 1), date(2026, 10, 1), date(2026, 10, 13))
        b = R.rotating_windows(date(2015, 1, 1), date(2026, 10, 1), date(2026, 10, 13))
        c = R.rotating_windows(date(2015, 1, 1), date(2026, 10, 1), date(2026, 10, 14))
        assert a == b and a != c and len(a) == 1

    def test_rotation_visits_every_month_within_one_cycle(self):
        n = len(R.month_windows(date(2015, 1, 1), date(2026, 10, 1)))
        seen = {R.rotating_windows(date(2015, 1, 1), date(2026, 10, 1), date.fromordinal(738000 + i))[0] for i in range(n)}
        assert len(seen) == n

    def test_several_months_per_run_are_spread_out(self):
        picks = R.rotating_windows(date(2015, 1, 1), date(2026, 10, 1), date(2026, 10, 13), n=3)
        assert len(picks) == 3 and len({p[0].year for p in picks}) >= 2


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
def test_capture_records_differences_and_leaves_daily_prices_alone(fake_yf, frozen_now):
    import psycopg
    frozen_now(NOW)
    with psycopg.connect(TEST_DB) as conn:
        conn.execute("DELETE FROM daily_prices WHERE ticker = 'INFY.NS' AND price_date BETWEEN '2005-02-01' AND '2005-02-28'")
        conn.execute("""INSERT INTO daily_prices (ticker, price_date, open, high, low, close, adj_close, volume) VALUES
                        ('INFY.NS', '2005-02-07', 100, 101, 99, 100, 100, 1000),
                        ('INFY.NS', '2005-02-08', 100, 101, 99, 100, 100, 1000)""")
        fake_yf["INFY.NS"] = FakeTicker(daily=bars(["2005-02-07", "2005-02-08", "2005-02-09"], [100, 102.5, 103],
                                                   volume=1000))
        run_id = conn.execute("INSERT INTO pipeline_runs (job_name) VALUES ('test_capture') RETURNING run_id").fetchone()[0]
        diffs = R.capture_window(conn, "INFY.NS", date(2005, 2, 1), date(2005, 2, 28), "rotating", run_id, None)
        kinds = sorted((str(x.price_date), x.kind, x.field) for x in diffs)
        assert ("2005-02-08", "value_changed", "close") in kinds and ("2005-02-09", "new_in_feed", None) in kinds
        stored = conn.execute("""SELECT close::float8 FROM daily_prices WHERE ticker = 'INFY.NS' AND price_date = '2005-02-08'""").fetchone()[0]
        assert stored == 100.0
        rows = conn.execute("""SELECT kind, field, stored_value::float8, fetched_value::float8 FROM revision_captures
                               WHERE ticker = 'INFY.NS' AND price_date = '2005-02-08' AND field = 'close'
                              AND run_id = %s""", (run_id,)).fetchall()
        assert rows == [("value_changed", "close", 100.0, 102.5)]
        obs = conn.execute("""SELECT COUNT(*) FROM price_observations WHERE ticker = 'INFY.NS'
                              AND price_date BETWEEN '2005-02-07' AND '2005-02-09'""").fetchone()[0]
        assert obs == 3
        cov = conn.execute("""SELECT rows_compared, rows_differing FROM revision_capture_coverage
                              WHERE run_id = %s""", (run_id,)).fetchone()
        assert cov[0] == 2 and cov[1] >= 2
        conn.execute("DELETE FROM daily_prices WHERE ticker = 'INFY.NS' AND price_date BETWEEN '2005-02-01' AND '2005-02-28'")
        conn.commit()

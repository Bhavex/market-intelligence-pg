from datetime import date

import pytest

import validation as V
from conftest import FakeConn

D = date


def row(day, close=100.0, o=None, h=None, l=None, adj=None, vol=1000, ticker="INFY.NS"):
    o = close if o is None else o
    h = max(close, o) * 1.01 if h is None else h
    l = min(close, o) * 0.99 if l is None else l
    adj = close if adj is None else adj
    return (ticker, day, o, h, l, close, adj, vol)


def series(closes, start_day=5):
    return [row(D(2026, 10, start_day + i), c) for i, c in enumerate(closes)]


def checks(result, severity="reject"):
    items = result.rejected if severity == "reject" else result.warnings
    return [(i.price_date, i.check) for i in items]


class TestCleanData:
    def test_clean_rows_all_pass(self):
        rows = series([100, 101, 102, 101, 103])
        r = V.validate_rows(rows)
        assert r.ok
        assert r.valid == rows
        assert r.warnings == []

    def test_result_is_sorted_by_ticker_and_date(self):
        rows = list(reversed(series([100, 101, 102])))
        assert [x[1] for x in V.validate_rows(rows).valid] == [D(2026, 10, 5), D(2026, 10, 6), D(2026, 10, 7)]

    def test_empty_input(self):
        r = V.validate_rows([])
        assert r.valid == [] and r.rejected == [] and r.summary().startswith("0 valid")

    def test_nine_column_rows_with_source_are_accepted(self):
        r = V.validate_rows([row(D(2026, 10, 5)) + ("yahoo_hourly",)])
        assert r.ok and r.valid[0][8] == "yahoo_hourly"


class TestMissingValues:
    @pytest.mark.parametrize("pos,name", [(2, "open"), (3, "high"), (4, "low"), (5, "close")])
    def test_missing_ohlc_is_rejected(self, pos, name):
        r = list(row(D(2026, 10, 5)))
        r[pos] = None
        res = V.validate_rows([tuple(r)])
        assert checks(res) == [(D(2026, 10, 5), V.MISSING_VALUE)]
        assert name in res.rejected[0].detail
        assert res.valid == []

    def test_nan_counts_as_missing(self):
        res = V.validate_rows([row(D(2026, 10, 5), h=float("nan"))])
        assert checks(res) == [(D(2026, 10, 5), V.MISSING_VALUE)]

    def test_missing_volume_and_adj_close_are_allowed(self):
        r = list(row(D(2026, 10, 5)))
        r[6], r[7] = None, None
        assert V.validate_rows([tuple(r)]).ok

    def test_malformed_row_is_rejected_not_raised(self):
        res = V.validate_rows([("INFY.NS", "2026-10-05", 1, 2, 3)])
        assert res.rejected[0].check == V.MALFORMED


class TestPriceSanity:
    @pytest.mark.parametrize("field", ["open", "high", "low", "close", "adj"])
    @pytest.mark.parametrize("value", [0.0, -5.0])
    def test_zero_or_negative_price_is_rejected(self, field, value):
        kw = {"o": None, "h": None, "l": None, "adj": None}
        if field == "close":
            r = row(D(2026, 10, 5), close=value, o=1.0, h=2.0, l=0.5 if value == 0 else -6.0)
        else:
            kw[{"open": "o", "high": "h", "low": "l", "adj": "adj"}[field]] = value
            r = row(D(2026, 10, 5), **kw)
        res = V.validate_rows([r])
        assert V.NON_POSITIVE_PRICE in [c for _, c in checks(res)]
        assert res.valid == []

    def test_negative_volume_is_rejected(self):
        res = V.validate_rows([row(D(2026, 10, 5), vol=-1)])
        assert checks(res) == [(D(2026, 10, 5), V.NEGATIVE_VOLUME)]

    def test_zero_volume_is_allowed(self):
        assert V.validate_rows([row(D(2026, 10, 5), vol=0)]).ok

    def test_high_below_low_is_rejected(self):
        res = V.validate_rows([row(D(2026, 10, 5), h=95.0, l=105.0)])
        assert checks(res) == [(D(2026, 10, 5), V.HIGH_BELOW_LOW)]

    def test_high_equal_low_is_allowed(self):
        assert V.validate_rows([row(D(2026, 10, 5), o=100, h=100, l=100)]).ok

    def test_close_outside_range_is_a_warning_only(self):
        res = V.validate_rows([row(D(2026, 10, 5), close=100, o=100, h=99.0, l=98.0)])
        assert res.ok
        assert checks(res, "warn") == [(D(2026, 10, 5), V.OUTSIDE_RANGE)]

    def test_tiny_rounding_outside_range_is_tolerated(self):
        assert V.validate_rows([row(D(2026, 10, 5), close=100.05, o=100, h=100.0, l=99.0)]).warnings == []


class TestDuplicates:
    def test_second_row_for_same_date_is_rejected_first_kept(self):
        a = row(D(2026, 10, 5), 100)
        b = row(D(2026, 10, 5), 101)
        res = V.validate_rows([a, b])
        assert res.valid == [a]
        assert checks(res) == [(D(2026, 10, 5), V.DUPLICATE)]

    def test_same_date_for_different_tickers_is_not_a_duplicate(self):
        res = V.validate_rows([row(D(2026, 10, 5)), row(D(2026, 10, 5), ticker="TCS.NS")])
        assert res.ok and len(res.valid) == 2


class TestJumps:
    def test_isolated_spike_that_reverts_is_rejected(self):
        res = V.validate_rows(series([100, 150, 101]))
        assert checks(res) == [(D(2026, 10, 6), V.PRICE_SPIKE)]
        assert [r[1] for r in res.valid] == [D(2026, 10, 5), D(2026, 10, 7)]

    def test_downward_spike_is_rejected(self):
        res = V.validate_rows(series([100, 40, 99]))
        assert checks(res) == [(D(2026, 10, 6), V.PRICE_SPIKE)]

    def test_persistent_jump_is_kept_with_warning(self):
        res = V.validate_rows(series([100, 130, 131, 129]))
        assert res.ok
        assert checks(res, "warn") == [(D(2026, 10, 6), V.PRICE_JUMP)]

    def test_jump_on_last_row_cannot_be_confirmed_and_is_kept(self):
        res = V.validate_rows(series([100, 101, 140]))
        assert res.ok and len(res.valid) == 3
        assert checks(res, "warn") == [(D(2026, 10, 7), V.PRICE_JUMP)]

    def test_move_below_threshold_is_silent(self):
        res = V.validate_rows(series([100, 119, 100]))
        assert res.ok and res.warnings == []

    def test_previous_stored_price_is_used_for_first_row(self):
        res = V.validate_rows(series([150, 101]), previous={"INFY.NS": (D(2026, 10, 2), 100.0)})
        assert checks(res) == [(D(2026, 10, 5), V.PRICE_SPIKE)]

    def test_rejection_does_not_cascade(self):
        res = V.validate_rows(series([100, 300, 100, 101, 102]))
        assert len(res.rejected) == 1 and len(res.valid) == 4

    def test_adj_close_is_used_so_dividend_adjusted_history_is_not_a_jump(self):
        rows = [row(D(2026, 10, 5), close=100, adj=50), row(D(2026, 10, 6), close=100, adj=50.5)]
        assert V.validate_rows(rows).warnings == []

    def test_threshold_is_configurable(self):
        cfg = V.ValidationConfig(jump_threshold=0.05)
        res = V.validate_rows(series([100, 110, 100]), config=cfg)
        assert checks(res) == [(D(2026, 10, 6), V.PRICE_SPIKE)]


class TestGaps:
    def test_missing_sessions_skips_weekends_and_holidays(self):
        assert V.missing_sessions(D(2026, 10, 9), D(2026, 10, 12)) == []
        assert V.missing_sessions(D(2026, 10, 1), D(2026, 10, 5), {D(2026, 10, 2)}) == []
        assert V.missing_sessions(D(2026, 10, 5), D(2026, 10, 9)) == [D(2026, 10, 6), D(2026, 10, 7), D(2026, 10, 8)]

    def test_large_gap_is_a_warning(self):
        rows = [row(D(2026, 10, 5)), row(D(2026, 10, 9))]
        res = V.validate_rows(rows)
        assert res.ok
        assert checks(res, "warn") == [(D(2026, 10, 9), V.TRADING_DAY_GAP)]

    def test_small_gap_below_threshold_is_silent(self):
        assert V.validate_rows([row(D(2026, 10, 5)), row(D(2026, 10, 8))]).warnings == []

    def test_holidays_are_not_gaps(self):
        rows = [row(D(2026, 10, 1)), row(D(2026, 10, 5))]
        assert V.validate_rows(rows, holidays={D(2026, 10, 2)}).warnings == []

    def test_gap_from_previous_stored_row_is_detected(self):
        res = V.validate_rows([row(D(2026, 10, 12))], previous={"INFY.NS": (D(2026, 10, 1), 100.0)})
        assert checks(res, "warn") == [(D(2026, 10, 12), V.TRADING_DAY_GAP)]


class TestSummary:
    def test_counts_and_summary(self):
        good = row(D(2026, 10, 5))
        res = V.validate_rows([row(D(2026, 10, 5), h=1, l=2), good])
        assert res.counts() == {V.HIGH_BELOW_LOW: 1}
        assert res.valid == [good]
        assert "1 valid, 1 rejected" in res.summary()


class TestDatabaseHelpers:
    def test_record_rejections_writes_rows(self):
        conn = FakeConn()
        res = V.validate_rows([row(D(2026, 10, 5), h=1, l=2)])
        assert V.record_rejections(conn, res.rejected, run_id=7) == 1
        run_id, ticker, day, check, detail, raw = conn.rejections[0]
        assert (run_id, ticker, day, check) == (7, "INFY.NS", D(2026, 10, 5), V.HIGH_BELOW_LOW)
        assert '"price_date": "2026-10-05"' in raw

    def test_record_rejections_without_table_is_a_noop(self):
        conn = FakeConn(tables=())
        res = V.validate_rows([row(D(2026, 10, 5), h=1, l=2)])
        assert V.record_rejections(conn, res.rejected) == 0
        assert conn.rejections == []

    def test_record_rejections_never_raises(self):
        conn = FakeConn()
        conn.add_rule("to_regclass", lambda p: (_ for _ in ()).throw(RuntimeError("db down")))
        assert V.record_rejections(conn, V.validate_rows([row(D(2026, 10, 5), h=1, l=2)]).rejected) == 0

    def test_load_holidays_falls_back_to_empty(self):
        conn = FakeConn()
        conn.add_rule("market_holidays", lambda p: (_ for _ in ()).throw(RuntimeError("missing")))
        assert V.load_holidays(conn) == set()

    def test_load_holidays(self):
        conn = FakeConn()
        conn.add_rule("market_holidays", [(D(2026, 10, 2),)])
        assert V.load_holidays(conn) == {D(2026, 10, 2)}

    def test_screen_splits_daily_and_fallback_and_uses_stored_previous(self):
        conn = FakeConn()
        conn.add_rule(r"FROM daily_prices WHERE ticker = %s AND price_date < %s", [(D(2026, 10, 2), 100.0)])
        daily = [row(D(2026, 10, 5), 160), row(D(2026, 10, 6), 101)]
        fallback = [row(D(2026, 10, 7), 102) + ("yahoo_hourly",)]
        out = V.screen(conn, "INFY.NS", daily, fallback, D(2026, 10, 5), run_id=3)
        assert [r[1] for r in out.daily] == [D(2026, 10, 6)]
        assert [r[1] for r in out.fallback] == [D(2026, 10, 7)]
        assert [i.check for i in out.result.rejected] == [V.PRICE_SPIKE]
        assert len(conn.rejections) == 1

import os
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import quality as Q
import strategy_backtest as B
import validation as V
from conftest import FakeConn

TEST_DB = os.environ.get("TEST_DATABASE_URL")
D = date


def at(d: date, hh=18, mm=0):
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=Q.IST)


def vrow(day, close, ticker="INFY.NS", **kw):
    h = kw.get("h", close * 1.01)
    l = kw.get("l", close * 0.99)
    return (ticker, day, close, h, l, close, close, 1000)


class TestIngest:
    def test_states_at_ingest(self):
        res = V.validate_rows([vrow(D(2026, 10, 5), 100), vrow(D(2026, 10, 6), 150), vrow(D(2026, 10, 7), 101, h=90, l=95)])
        states = {(t.price_date, t.state) for t in Q.ingest_states("INFY.NS", res)}
        assert states == {(D(2026, 10, 5), Q.PROVISIONAL), (D(2026, 10, 6), Q.SUSPECT), (D(2026, 10, 7), Q.REJECTED)}


class TestTransitions:
    def series(self, closes, states, start=D(2026, 10, 5)):
        days = pd.bdate_range(start, periods=len(closes)).date
        return [(d, c, c, s) for d, c, s in zip(days, closes, states)]

    def test_spike_that_reverses_next_session_is_rejected_later(self):
        s = self.series([100, 150, 101], [Q.ACCEPTED, Q.SUSPECT, Q.PROVISIONAL])
        t = Q.decide_transitions("INFY.NS", s, at(D(2026, 10, 7)))
        assert [(x.price_date, x.state, x.reason) for x in t] == [(D(2026, 10, 6), Q.REJECTED, "reversed_next_session")]
        assert t[0].evidence["next_date"] == "2026-10-07"

    def test_suspect_without_next_session_stays_suspect(self):
        s = self.series([100, 150], [Q.ACCEPTED, Q.SUSPECT])
        assert Q.decide_transitions("INFY.NS", s, at(D(2026, 10, 6))) == []

    def test_persistent_jump_is_accepted_and_the_next_row_downgraded(self):
        s = self.series([100, 130, 131], [Q.ACCEPTED, Q.SUSPECT, Q.SUSPECT])
        t = Q.decide_transitions("INFY.NS", s, at(D(2026, 10, 7)))
        assert [(x.price_date, x.state, x.reason) for x in t] == [
            (D(2026, 10, 6), Q.ACCEPTED, "jump_persisted"), (D(2026, 10, 7), Q.PROVISIONAL, "reference_confirmed")]

    def test_provisional_is_accepted_when_a_later_fetch_reconfirms_it(self):
        s = self.series([100, 101], [Q.ACCEPTED, Q.PROVISIONAL])
        refetched = {D(2026, 10, 6): 101.0}
        assert Q.decide_transitions("INFY.NS", s, at(D(2026, 10, 6), 18), refetched) == []
        t = Q.decide_transitions("INFY.NS", s, at(D(2026, 10, 7), 18), refetched)
        assert [(x.price_date, x.state) for x in t] == [(D(2026, 10, 6), Q.ACCEPTED)]

    def test_a_revised_value_is_not_reconfirmed(self):
        s = self.series([100, 101], [Q.ACCEPTED, Q.PROVISIONAL])
        assert Q.decide_transitions("INFY.NS", s, at(D(2026, 10, 8)), {D(2026, 10, 6): 102.0}) == []

    def test_a_distant_row_is_not_the_next_session(self):
        s = [(D(2026, 10, 5), 100, 100, Q.ACCEPTED), (D(2026, 10, 6), 150, 150, Q.SUSPECT),
             (D(2027, 3, 1), 151, 151, Q.PROVISIONAL)]
        assert Q.decide_transitions("INFY.NS", s, at(D(2027, 3, 2))) == []

    def test_rejected_rows_are_never_the_reference(self):
        s = self.series([100, 150, 101, 160], [Q.ACCEPTED, Q.REJECTED, Q.PROVISIONAL, Q.SUSPECT])
        assert Q.decide_transitions("INFY.NS", s, at(D(2026, 10, 8))) == []

    def test_unassessed_legacy_rows_are_left_alone(self):
        s = self.series([100, 101], [None, None])
        assert Q.decide_transitions("INFY.NS", s, at(D(2026, 10, 9)), {D(2026, 10, 6): 101.0}) == []


class TestChangedOnly:
    def conn(self, current):
        c = FakeConn(tables=("quality_states",))
        c.add_rule("FROM v_quality_current", current)
        return c

    def test_re_fetching_an_accepted_value_does_not_reset_it(self):
        conn = self.conn([("INFY.NS", D(2026, 10, 6), Q.ACCEPTED, 101.0)])
        t = [Q.Transition("INFY.NS", D(2026, 10, 6), Q.PROVISIONAL, "new_observation", {}, 101.0)]
        assert Q.changed_only(conn, t) == []

    def test_a_revised_value_starts_a_new_state(self):
        conn = self.conn([("INFY.NS", D(2026, 10, 6), Q.ACCEPTED, 101.0)])
        t = [Q.Transition("INFY.NS", D(2026, 10, 6), Q.PROVISIONAL, "new_observation", {}, 103.0)]
        out = Q.changed_only(conn, t)
        assert out[0].reason == "revised_value" and out[0].evidence["previous_state"] == Q.ACCEPTED

    def test_a_bad_re_fetch_does_not_reject_the_stored_row(self):
        conn = self.conn([("INFY.NS", D(2026, 10, 6), Q.ACCEPTED, 101.0)])
        t = [Q.Transition("INFY.NS", D(2026, 10, 6), Q.REJECTED, "high_below_low", {}, 101.0)]
        assert Q.changed_only(conn, t) == []


def states_frame(rows):
    return pd.DataFrame(rows, columns=["ticker", "price_date", "state", "decided_at"])


class TestPointInTimeBacktest:
    def panel(self):
        idx = pd.bdate_range("2024-01-01", periods=90)
        rng = np.random.default_rng(0)
        px = pd.DataFrame({t: 100 * np.cumprod(1 + rng.normal(0, 0.002, len(idx))) for t in "ABCD"}, index=idx)
        signal_day = B.signal_dates(px.index)[2]
        px.loc[signal_day, "A"] *= 3
        return px, signal_day

    def test_timeline_reports_the_state_known_at_each_time(self):
        day = pd.Timestamp("2024-03-29")
        tl = B.QualityTimeline(states_frame([
            ("A", day, "suspect", pd.Timestamp("2024-03-29 12:00", tz="UTC")),
            ("A", day, "accepted", pd.Timestamp("2024-04-01 12:00", tz="UTC")),
        ]))
        assert tl.excluded_at(pd.Timestamp("2024-03-29 18:00", tz="UTC")) == [("A", day)]
        assert tl.excluded_at(pd.Timestamp("2024-04-02", tz="UTC")) == []
        assert tl.excluded_at(pd.Timestamp("2024-03-28", tz="UTC")) == []
        assert len(tl.states) == 2

    def test_decision_ignores_a_row_that_was_suspect_at_the_time(self):
        px, day = self.panel()
        fn = lambda p: B.momentum_weights(p, 20, 1)
        cfg = B.BacktestConfig(warmup_days=20)
        blind = B.simulate(px, fn, cfg, "blind")
        assert blind.holdings[day] == ["A"]
        tl = B.QualityTimeline(states_frame([("A", day, "suspect", pd.Timestamp(day.date()).tz_localize("UTC") + pd.Timedelta(hours=11))]))
        aware = B.simulate(px, fn, cfg, "aware", timeline=tl)
        assert aware.holdings[day] != ["A"]

    def test_a_state_decided_after_the_decision_does_not_leak_back(self):
        px, day = self.panel()
        later = pd.Timestamp(day.date()).tz_localize("UTC") + pd.Timedelta(days=3)
        tl = B.QualityTimeline(states_frame([("A", day, "rejected", later)]))
        res = B.simulate(px, lambda p: B.momentum_weights(p, 20, 1), B.BacktestConfig(warmup_days=20), "pit", timeline=tl)
        assert res.holdings[day] == ["A"]

    def test_empty_timeline_changes_nothing(self):
        px, _ = self.panel()
        fn = lambda p: B.momentum_weights(p, 20, 2)
        a = B.simulate(px, fn, B.BacktestConfig(warmup_days=20), "a")
        b = B.simulate(px, fn, B.BacktestConfig(warmup_days=20), "b", timeline=B.QualityTimeline())
        assert a.holdings == b.holdings


@pytest.fixture
def conn():
    import psycopg
    c = psycopg.connect(TEST_DB)
    yield c
    c.rollback()
    c.close()


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
class TestDatabase:
    def test_states_are_append_only(self, conn):
        import psycopg
        conn.execute("""INSERT INTO quality_states (ticker, price_date, state, reason, rule_version)
                        VALUES ('INFY.NS', '2003-01-06', 'provisional', 'new_observation', 'quality-v2')""")
        for sql in ("UPDATE quality_states SET state = 'accepted' WHERE price_date = '2003-01-06'",
                    "DELETE FROM quality_states WHERE price_date = '2003-01-06'"):
            with pytest.raises(psycopg.errors.RestrictViolation):
                with conn.transaction():
                    conn.execute(sql)

    def test_earlier_state_stays_visible_as_of_its_time(self, conn):
        conn.execute("""INSERT INTO quality_states (ticker, price_date, state, reason, rule_version, decided_at) VALUES
                        ('INFY.NS', '2003-01-07', 'suspect', 'jump_vs_reference', 'quality-v2', '2003-01-07 12:00+00'),
                        ('INFY.NS', '2003-01-07', 'rejected', 'reversed_next_session', 'quality-v2', '2003-01-08 12:00+00')""")
        then = conn.execute("""SELECT state FROM quality_as_of('2003-01-07 18:00+00')
                               WHERE ticker = 'INFY.NS' AND price_date = '2003-01-07'""").fetchone()[0]
        now = conn.execute("""SELECT state FROM v_quality_current
                              WHERE ticker = 'INFY.NS' AND price_date = '2003-01-07'""").fetchone()[0]
        assert (then, now) == ("suspect", "rejected")
        assert conn.execute("""SELECT COUNT(*) FROM quality_states
                               WHERE ticker = 'INFY.NS' AND price_date = '2003-01-07'""").fetchone()[0] == 2

    def test_pipeline_records_then_revises_a_spike(self, conn):
        import prices
        conn.execute("DELETE FROM daily_prices WHERE ticker = 'INFY.NS' AND price_date BETWEEN '2003-01-01' AND '2003-01-31'")
        day1 = [vrow(D(2003, 1, 13), 100), vrow(D(2003, 1, 14), 150)]
        r1 = V.validate_rows(day1)
        prices.upsert_rows(conn, r1.valid, commit=False)
        Q.assess_ticker(conn, "INFY.NS", r1, D(2003, 1, 13), now=at(D(2003, 1, 14), 18))
        day2 = day1 + [vrow(D(2003, 1, 15), 101)]
        r2 = V.validate_rows(day2)
        prices.upsert_rows(conn, r2.valid, commit=False)
        Q.assess_ticker(conn, "INFY.NS", r2, D(2003, 1, 13), now=at(D(2003, 1, 15), 18))
        hist = conn.execute("""SELECT state, reason FROM quality_states
                               WHERE ticker = 'INFY.NS' AND price_date = '2003-01-14' ORDER BY decided_at, state_id""").fetchall()
        assert hist == [("suspect", "jump_vs_reference"), ("rejected", "reversed_next_session")]
        first = conn.execute("""SELECT state FROM v_quality_current
                                WHERE ticker = 'INFY.NS' AND price_date = '2003-01-13'""").fetchone()[0]
        assert first == "accepted"

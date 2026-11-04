import os
from datetime import date

import numpy as np
import pandas as pd
import pytest

import strategy_backtest as B

TEST_DB = os.environ.get("TEST_DATABASE_URL")
SWITCH = pd.Timestamp("2024-03-15")


def membership():
    return B.Membership(pd.DataFrame([
        ("X", "2024-01-01", SWITCH), ("Y", SWITCH, None), ("Z", "2024-01-01", None), ("W", "2024-01-01", None)],
        columns=["ticker", "valid_from", "valid_to"]))


def panel():
    idx = pd.bdate_range("2024-01-01", periods=140)
    rng = np.random.default_rng(3)
    drift = {"X": 0.004, "Y": 0.006, "Z": 0.0, "W": -0.001}
    return pd.DataFrame({t: 100 * np.cumprod(1 + mu + rng.normal(0, 0.002, len(idx))) for t, mu in drift.items()}, index=idx)


class TestMembership:
    def test_members_on_each_side_of_the_switch(self):
        m = membership()
        assert m.members("2024-03-14") == {"X", "Z", "W"}
        assert m.members(SWITCH) == {"Y", "Z", "W"}
        assert SWITCH in m.change_dates()

    def test_decisions_only_use_members_known_on_the_decision_date(self):
        px = panel()
        res = B.simulate(px, lambda p: B.momentum_weights(p, 20, 1), B.BacktestConfig(warmup_days=20), "mom",
                         membership=membership())
        for d, held in res.holdings.items():
            assert set(held) <= membership().members(d)
        before = [h for d, h in res.holdings.items() if d < SWITCH]
        after = [h for d, h in res.holdings.items() if d >= SWITCH]
        assert before and after and all("Y" not in h for h in before) and all("X" not in h for h in after)

    def test_without_membership_the_future_joiner_leaks_into_early_decisions(self):
        px = panel()
        res = B.simulate(px, lambda p: B.momentum_weights(p, 20, 1), B.BacktestConfig(warmup_days=20), "mom")
        assert any("Y" in h for d, h in res.holdings.items() if d < SWITCH)

    def test_buy_and_hold_hands_the_leavers_weight_to_the_joiner(self):
        px = panel()
        res = B.simulate(px, B.equal_weights, B.BacktestConfig(warmup_days=20), "bh", rebalance="once",
                         membership=membership())
        entry = sorted(res.holdings)[0]
        assert set(res.holdings[entry]) == {"X", "Z", "W"}
        swap = [d for d in res.holdings if d >= SWITCH]
        assert swap and set(res.holdings[swap[0]]) == {"Y", "Z", "W"}
        assert res.turnover.sum() > 1.0


@pytest.fixture
def conn():
    import psycopg
    c = psycopg.connect(TEST_DB)
    yield c
    c.rollback()
    c.close()


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
class TestDatabase:
    def test_every_company_has_a_stable_instrument(self, conn):
        assert conn.execute("SELECT COUNT(*) FROM companies WHERE instrument_id IS NULL").fetchone()[0] == 0
        n = conn.execute("SELECT COUNT(DISTINCT instrument_id) FROM companies").fetchone()[0]
        assert n == conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    def test_tata_keeps_its_history_with_an_end_date_and_hero_starts_then(self, conn):
        switch, evidence = conn.execute("SELECT switch_date, evidence FROM v_tata_hero_switch").fetchone()
        rows = {t: (a, b) for t, a, b in conn.execute("""SELECT ticker, valid_from, valid_to FROM v_universe_membership
                                    WHERE universe = 'dashboard_25' AND ticker IN ('TATAMOTORS.NS', 'HEROMOTOCO.NS')""").fetchall()}
        assert rows["TATAMOTORS.NS"] == (date(2015, 1, 1), switch)
        assert rows["HEROMOTOCO.NS"] == (switch, None)
        assert evidence in ("audit_log", "last_price_date", "fallback_estimate")

    def test_universe_has_25_members_on_both_sides_of_the_switch(self, conn):
        switch = conn.execute("SELECT switch_date FROM v_tata_hero_switch").fetchone()[0]
        for d in (date(2020, 6, 1), switch, date(2026, 6, 1)):
            members = {r[0] for r in conn.execute("SELECT ticker FROM universe_members('dashboard_25', %s)", (d,))}
            assert len(members) == 25
            assert ("TATAMOTORS.NS" in members) == (d < switch)
            assert ("HEROMOTOCO.NS" in members) == (d >= switch)

    def test_overlapping_membership_is_refused(self, conn):
        import psycopg
        iid = conn.execute("SELECT instrument_id FROM companies WHERE ticker = 'INFY.NS'").fetchone()[0]
        with pytest.raises((psycopg.errors.ExclusionViolation, psycopg.errors.UniqueViolation)):
            with conn.transaction():
                conn.execute("""INSERT INTO universe_membership (universe, instrument_id, valid_from, reason)
                                VALUES ('dashboard_25', %s, '2020-01-01', 'duplicate')""", (iid,))

    def test_replace_company_ends_and_starts_membership(self, conn):
        conn.execute("CALL replace_company('LUPIN.NS', 'NEWPHARMA.NS', 'New Pharma Ltd')")
        old = conn.execute("""SELECT valid_to FROM v_universe_membership WHERE universe = 'dashboard_25'
                              AND ticker = 'LUPIN.NS'""").fetchone()[0]
        new = conn.execute("""SELECT valid_from, valid_to FROM v_universe_membership WHERE universe = 'dashboard_25'
                              AND ticker = 'NEWPHARMA.NS'""").fetchone()
        today = conn.execute("SELECT CURRENT_DATE").fetchone()[0]
        assert old == today and new == (today, None)

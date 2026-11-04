import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import strategy_backtest as B

ROOT = Path(__file__).resolve().parent.parent


def panel(n=300, tickers=("A", "B", "C", "D"), seed=0, drift=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    data = {}
    for i, t in enumerate(tickers):
        mu = 0.0 if drift is None else drift.get(t, 0.0)
        data[t] = 100 * np.cumprod(1 + mu + rng.normal(0, 0.01, n))
    return pd.DataFrame(data, index=idx)


SECTORS = {"A": "X", "B": "X", "C": "Y", "D": "Y"}


class TestNoLookAhead:
    def test_weight_function_only_sees_data_up_to_the_signal_date(self):
        px = panel()
        seen = []

        def spy(p):
            seen.append(p.index[-1])
            return B.equal_weights(p)

        B.simulate(px, spy, B.BacktestConfig(warmup_days=20), "spy")
        month_ends = set(B.signal_dates(px.index))
        assert seen and all(d in month_ends for d in seen)

    def test_changing_future_prices_does_not_change_past_decisions(self):
        px = panel()
        cfg = B.BacktestConfig(warmup_days=20, momentum_lookback=20, momentum_top_n=2)
        fn = lambda p: B.momentum_weights(p, 20, 2)
        a = B.simulate(px, fn, cfg, "a")
        cut = px.index[200]
        shocked = px.copy()
        shocked.loc[shocked.index > cut] *= np.linspace(0.2, 5, 4)
        b = B.simulate(shocked, fn, cfg, "b")
        early = {d: h for d, h in a.holdings.items() if d <= cut}
        assert early and early == {d: h for d, h in b.holdings.items() if d <= cut}

    def test_trades_happen_after_the_signal_close(self):
        px = panel()
        res = B.simulate(px, B.equal_weights, B.BacktestConfig(warmup_days=20, lag_days=1), "ew")
        traded = res.turnover[res.turnover > 0].index
        signals = B.signal_dates(px.index)
        for d in traded:
            prev = px.index[px.index.get_loc(d) - 1]
            assert prev in signals and d not in signals

    def test_first_return_starts_on_the_execution_day(self):
        px = panel()
        res = B.simulate(px, B.equal_weights, B.BacktestConfig(warmup_days=20), "ew", rebalance="once")
        assert res.returns.index[0] == res.turnover[res.turnover > 0].index[0]


class TestCosts:
    def test_flat_prices_lose_exactly_the_costs(self):
        px = pd.DataFrame(100.0, index=pd.bdate_range("2024-01-01", periods=200), columns=list("ABCD"))
        cfg = B.BacktestConfig(warmup_days=20, cost_bp=10, momentum_lookback=20, momentum_top_n=2)
        res = B.simulate(px, B.equal_weights, cfg, "ew", rebalance="once")
        assert res.turnover.sum() == pytest.approx(0.999000999)
        assert (1 + res.returns).prod() - 1 == pytest.approx(-0.000999000999)

    def test_cost_scales_with_turnover(self):
        px = panel(seed=3)
        fn = lambda p: B.momentum_weights(p, 20, 1)
        free = B.simulate(px, fn, B.BacktestConfig(warmup_days=20, cost_bp=0), "free")
        paid = B.simulate(px, fn, B.BacktestConfig(warmup_days=20, cost_bp=50), "paid")
        assert (free.gross_returns == paid.gross_returns).all()
        assert (1 + paid.returns).prod() < (1 + free.returns).prod()
        assert free.turnover.sum() * 0.99 < paid.turnover.sum() <= free.turnover.sum()

    def test_apply_cost_to_picks(self):
        gross = pd.Series([0.0, 0.0, 0.0])
        picks = pd.Series(["IT", "IT", "Auto"])
        net = B.apply_cost_to_picks(gross, picks, 10)
        assert net.tolist() == pytest.approx([-0.001, 0.0, -0.002])

    def test_published_series_is_reproduced(self):
        res = B.published_rotation(ROOT / "results" / "revision" / "task6_monthly_clean.csv")
        assert res["reproduction_error_10bp"] < 1e-5
        assert res["metrics"]["rotation_gross"]["sharpe"] == pytest.approx(0.7198, abs=1e-3)
        assert res["metrics"]["rotation_net"]["final_value_of_100"] < res["metrics"]["rotation_gross"]["final_value_of_100"]


class TestSignals:
    def test_momentum_picks_the_strongest(self):
        px = panel(drift={"C": 0.004})
        w = B.momentum_weights(px, 60, 1)
        assert list(w.index) == ["C"] and w.sum() == pytest.approx(1)

    def test_sector_strength_holds_every_stock_of_the_leading_sector(self):
        px = panel(drift={"C": 0.003, "D": 0.003})
        w = B.sector_strength_weights(px, SECTORS, 20, 1)
        assert sorted(w.index) == ["C", "D"] and w.tolist() == [0.5, 0.5]

    def test_planted_trend_is_detected(self):
        px = panel(n=600, drift={"C": 0.002, "D": 0.002}, seed=5)
        res = B.run_all(px, SECTORS, B.BacktestConfig(warmup_days=30))
        table = B.summarize(res)
        assert table.loc["sector_strength", "cagr"] > table.loc["benchmark", "cagr"]

    def test_not_enough_history_gives_no_position(self):
        assert B.momentum_weights(panel(n=10), 60, 5).empty


class TestMetrics:
    def test_max_drawdown(self):
        assert B.max_drawdown(pd.Series([0.1, -0.5, 0.2])) == pytest.approx(-0.5)

    def test_sharpe_and_cagr(self):
        r = pd.Series([0.01, -0.005] * 126, index=pd.bdate_range("2024-01-01", periods=252))
        m = B.metrics(r)
        assert m["sharpe"] == pytest.approx(r.mean() * 252 / (r.std() * math.sqrt(252)))
        assert m["cagr"] == pytest.approx((1 + r).prod() - 1)

    def test_hit_rate_against_benchmark(self):
        idx = pd.period_range("2024-01", periods=4, freq="M")
        s = pd.Series([0.02, -0.01, 0.03, 0.0], index=idx)
        b = pd.Series([0.01, 0.00, 0.01, 0.01], index=idx)
        assert B.metrics(s, b, periods_per_year=12)["hit_rate_vs_benchmark"] == 0.5

    def test_synthetic_run_is_deterministic(self):
        px, sectors = B.load_synthetic_panel(seed=1, years=3)
        a = B.summarize(B.run_all(px, sectors))
        b = B.summarize(B.run_all(px, sectors))
        pd.testing.assert_frame_equal(a, b)

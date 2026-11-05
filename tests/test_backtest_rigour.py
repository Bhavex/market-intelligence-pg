import json
import os
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

import backtest_rigour as R
import strategy_backtest as B

TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.fixture(scope="module")
def panel():
    return B.load_synthetic_panel(seed=3, years=4)


class TestBootstrap:
    def test_blocks_are_contiguous_and_wrap_around(self):
        idx = R.block_indices(10, 4, 50, np.random.default_rng(1))
        assert idx.shape == (50, 10)
        for row in idx:
            for start in range(0, 10, 4):
                block = row[start:start + 4]
                assert all((b - a) % 10 == 1 for a, b in zip(block, block[1:]))

    def test_constant_returns_give_a_zero_width_interval(self):
        r = pd.DataFrame({"s": [0.001] * 252, "benchmark": [0.0005] * 252},
                         index=pd.bdate_range("2024-01-01", periods=252))
        out = R.bootstrap(r, draws=200, block=21).set_index(["strategy", "metric"])
        cagr = out.loc[("s", "cagr")]
        assert cagr["lo"] == pytest.approx(cagr["hi"]) == pytest.approx(1.001 ** 252 - 1)
        assert out.loc[("s", "max_drawdown"), "point"] == 0
        assert out.loc[("s", "excess_cagr_vs_benchmark"), "share_above_zero"] == 1.0

    def test_interval_brackets_the_point_estimate(self, panel):
        px, sectors = panel
        res = B.run_all(px, sectors, B.BacktestConfig())
        r = pd.DataFrame({k: v.returns for k, v in res.items()}).dropna()
        out = R.bootstrap(r, draws=300, block=21, seed=4)
        main = out[out["metric"].isin(["cagr", "sharpe", "max_drawdown"])]
        assert ((main["lo"] <= main["point"]) & (main["point"] <= main["hi"])).all()
        assert out.attrs == {"block": 21, "draws": 300, "seed": 4, "level": 0.95, "observations": len(r)}

    def test_same_seed_same_interval(self, panel):
        px, sectors = panel
        r = pd.DataFrame({"b": px.pct_change().mean(axis=1).fillna(0)})
        a, b = R.bootstrap(r, None, seed=9, draws=100), R.bootstrap(r, None, seed=9, draws=100)
        pd.testing.assert_frame_equal(a, b)


class TestDeflatedSharpe:
    def test_one_trial_reduces_to_the_probabilistic_sharpe_ratio(self):
        r = pd.Series([0.011, -0.009] * 50)
        d = R.deflated_sharpe(r, [0.1])
        assert d["trials"] == 1 and d["expected_max_sharpe_annual"] == 0
        assert d["kurtosis"] == pytest.approx(1.0) and d["skew"] == pytest.approx(0.0, abs=1e-9)
        assert d["deflated_sharpe_probability"] == pytest.approx(0.838913, abs=1e-5)

    def test_expected_maximum_sharpe_of_ten_trials(self):
        trials = np.random.default_rng(0).normal(0, 0.05, 10)
        d = R.deflated_sharpe(pd.Series([0.011, -0.009] * 50), trials)
        assert d["expected_max_sharpe_annual"] / d["trial_sharpe_sd_annual"] == pytest.approx(1.574598, abs=1e-5)

    def test_more_trials_lower_the_probability(self):
        r = pd.Series(np.random.default_rng(2).normal(0.0008, 0.01, 750))
        few = R.deflated_sharpe(r, np.random.default_rng(3).normal(0, 0.03, 3))
        many = R.deflated_sharpe(r, np.random.default_rng(3).normal(0, 0.03, 300))
        assert many["deflated_sharpe_probability"] < few["deflated_sharpe_probability"]


class TestSplitAndCosts:
    def test_holdout_is_the_final_period_and_disjoint(self, panel):
        px, _ = panel
        cut = R.holdout_start(px.index, 1.0)
        r = pd.Series(1.0, index=px.index)
        dev, hold = R.period_slice(r, "development", cut), R.period_slice(r, "holdout", cut)
        assert len(dev) + len(hold) == len(r) and dev.index.max() < hold.index.min() == cut
        assert (px.index[-1] - cut).days <= 366

    def test_higher_costs_never_raise_returns(self, tmp_path, panel):
        px, sectors = panel
        ev = R.Evaluator(px, sectors, B.BacktestConfig(), R.CsvRegistry(tmp_path / "t.csv"), "synthetic", 1.0)
        costs, by_level = ev.cost_sensitivity()
        full = costs[costs["period"] == "full"].pivot(index="strategy", columns="cost_level", values="cagr")
        assert (full["zero"] >= full["realistic"]).all() and (full["realistic"] >= full["high"]).all()
        assert R.COST_LEVELS == {"zero": 0.0, "realistic": 11.2, "high": 41.2}
        rep = R.turnover_report(by_level["realistic"], replace(B.BacktestConfig(), cost_bp=11.2))
        assert rep.loc["benchmark", "trade_days"] == 1
        assert rep.loc["momentum", "turnover_per_year"] > rep.loc["equal_weight_rebalanced", "turnover_per_year"]
        assert (rep["cost_drag_cagr"] >= 0).all()

    def test_selection_never_sees_the_holdout(self, tmp_path, panel):
        px, sectors = panel
        base = B.BacktestConfig()
        cut = R.holdout_start(px.index, 1.0)
        bench = B.simulate(px, B.equal_weights, base, "benchmark", rebalance="once")
        _, chosen = R.Evaluator(px, sectors, base, None, "synthetic", 1.0).sweep(bench)
        shocked = px.copy()
        shocked.loc[shocked.index >= cut] *= np.random.default_rng(5).uniform(0.3, 3.0, size=px.shape[1])
        bench2 = B.simulate(shocked, B.equal_weights, base, "benchmark", rebalance="once")
        _, chosen2 = R.Evaluator(shocked, sectors, base, None, "synthetic", 1.0).sweep(bench2)
        assert {k: v["params"] for k, v in chosen.items()} == {k: v["params"] for k, v in chosen2.items()}


class TestRegistry:
    def test_every_evaluation_is_appended_and_never_forgotten(self, tmp_path, panel):
        px, sectors = panel
        reg = R.CsvRegistry(tmp_path / "trials.csv")
        first = R.run(px, sectors, B.BacktestConfig(), tmp_path / "a", reg, "synthetic", 1.0, draws=50)
        n1 = len(reg.trials())
        expected = 3 * 4 * 3 + sum(len(g) for g in R.SWEEP.values()) + len(R.SWEEP)
        assert n1 == first["summary"]["trials_this_run"] == expected
        R.run(px, sectors, B.BacktestConfig(), tmp_path / "b", reg, "synthetic", 1.0, draws=50)
        df = reg.trials()
        assert len(df) == 2 * n1 and df["trial_id"].astype(int).tolist() == list(range(1, 2 * n1 + 1))
        assert df["run_tag"].nunique() >= 1 and set(df["period"]) == set(R.PERIODS)
        assert (df[df["period"] == "holdout"]["purpose"].isin(["cost_sensitivity", "selected_on_development"])).all()
        assert first["summary"]["deflated_sharpe"]["momentum"]["trials"] == 3 + len(R.SWEEP["momentum"])

    def test_outputs_are_written(self, tmp_path, panel):
        px, sectors = panel
        out = R.run(px, sectors, B.BacktestConfig(), tmp_path, R.CsvRegistry(tmp_path / "trials.csv"), "synthetic", 1.0,
                    draws=50)
        for name in ("cost_sensitivity.csv", "turnover.csv", "bootstrap_ci.csv", "parameter_sweep.csv",
                     "trials_this_run.json", "rigour_summary.json", "rigour_summary.md"):
            assert (tmp_path / name).exists(), name
        md = out["markdown"]
        for heading in ("Cost sensitivity", "Turnover", "Block bootstrap", "Chronological split", "development period only"):
            assert heading in md
        summary = json.loads((tmp_path / "rigour_summary.json").read_text())
        assert summary["holdout_start"] == str(R.holdout_start(px.index, 1.0).date())

    @pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
    def test_database_registry_is_append_only(self, panel):
        import psycopg
        px, sectors = panel
        with psycopg.connect(TEST_DB) as conn:
            assert R.DbRegistry.available(conn)
            reg = R.DbRegistry(conn)
            before = len(reg.trials())
            ev = R.Evaluator(px, sectors, B.BacktestConfig(), reg, "synthetic", 1.0, run_tag="pytest")
            res = ev.simulate("benchmark", B.BacktestConfig())
            ev.record("benchmark", B.BacktestConfig(), res, "holdout", "realistic", "test")
            df = reg.trials()
            assert len(df) == before + 1 and df.iloc[-1]["metrics"]["cagr"] is not None
            counts = conn.execute("""SELECT evaluations, holdout_evaluations FROM v_backtest_trial_counts
                                     WHERE strategy = 'benchmark' AND source = 'synthetic'""").fetchone()
            assert counts[0] >= 1 and counts[1] >= 1
            with pytest.raises(psycopg.errors.RestrictViolation):
                conn.execute("DELETE FROM backtest_trials")
            conn.rollback()

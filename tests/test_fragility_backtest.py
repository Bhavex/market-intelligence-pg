"""The restated month-end backtest (fragility.MonthEndBacktest) must equal fragility.monthly_backtest (no database)."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fragility as fr  # noqa: E402


def synthetic(seed=0, days=420, per_sector=3, sectors=("IT", "Bank", "Auto"), wick=0.006):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=days)
    rows = []
    for si, sec in enumerate(sectors):
        for k in range(per_sector):
            close = 100 * np.exp(np.cumsum(rng.normal(0.0004 * (si + 1), 0.015, days)))
            op = close * (1 + rng.normal(0, 0.004, days))
            hi, lo = np.maximum(op, close) * (1 + wick), np.minimum(op, close) * (1 - wick)
            for i, d in enumerate(dates):
                rows.append((f"{sec}{k}.NS", d, op[i], hi[i], lo[i], close[i], close[i], 1e6 * (1 + rng.random()), sec, True, "large"))
    p = pd.DataFrame(rows, columns=["ticker", "price_date", "open", "high", "low", "close", "adj_close", "volume",
                                    "sector_name", "in_dashboard", "cap_group"])
    p.loc[(p.ticker == "IT1.NS") & (p.price_date < "2023-02-01"), "adj_close"] = np.nan     # a gap: NaN momentum early on
    return p


class Restated(unittest.TestCase):
    def setUp(self):
        self.f = fr.features(synthetic())
        self.base = fr.day_signals(self.f, self.f["px"].values, self.f["volume"].values)
        self.ret = self.f["px"].values / self.f["px_prev"].values - 1
        self.model = fr.MonthEndBacktest(self.f, self.base["mom20"], self.ret)

    def test_baseline_equals_monthly_backtest(self):
        v0, picks0 = fr.monthly_backtest(self.f, self.base["mom20"], self.ret)
        self.assertAlmostEqual(self.model.values(self.model.base_mom())[0], v0, places=8)
        self.assertEqual(self.model.n_months, len(picks0))

    def test_perturbed_month_ends_equal_monthly_backtest(self):
        rng = np.random.default_rng(1)
        dates = self.f["price_date"].values
        me = np.array(list(set(self.f.groupby(self.f["price_date"].dt.to_period("M"))["price_date"].max())), dtype="datetime64[ns]")
        for _ in range(8):
            hit = np.isin(dates, me[rng.random(len(me)) < 0.5])
            px = np.where(hit, self.f["px"].values * np.exp(rng.normal(0, 0.08, len(self.f))), self.f["px"].values)
            pert = fr.day_signals(self.f, px, self.f["volume"].values)
            v_old, picks_old = fr.monthly_backtest(self.f, pert["mom20"], self.ret)
            mom = (pert["mom20"][self.model.rows])[None, :]
            self.assertAlmostEqual(self.model.values(mom)[0], v_old, places=8)
            idx = self.model.picks(mom)[0]
            months = [m + 1 for m in self.model.months]
            self.assertEqual([self.model.sectors[i] for i in idx], [picks_old[m] for m in months])

    def test_draws_are_reproducible_and_a_zero_rate_changes_nothing(self):
        a = self.model.values(self.model.draw_mom(np.random.default_rng(5), 20, 1.0))
        b = self.model.values(self.model.draw_mom(np.random.default_rng(5), 20, 1.0))
        np.testing.assert_array_equal(a, b)
        none = self.model.values(self.model.draw_mom(np.random.default_rng(5), 20, 0.0))
        np.testing.assert_allclose(none, self.model.values(self.model.base_mom())[0])


class Bridge(unittest.TestCase):
    def test_core_matches_the_rowwise_function(self):
        f = fr.features(synthetic(days=60))
        a = fr.bridge_price(f, np.random.default_rng(3), np.full(len(f), 0.4))
        z = np.random.default_rng(3).standard_normal(len(f))
        b = fr.bridge_core(f["open"].values, f["close"].values, f["high"].values, f["low"].values, 0.4, z)
        np.testing.assert_array_equal(a, b)


class Stability(unittest.TestCase):
    def test_replicates_report_their_spread_and_use_independent_streams(self):
        f = fr.features(synthetic(wick=0.15))      # wide intraday ranges so that wrong month-ends do change picks
        base = fr.day_signals(f, f["px"].values, f["volume"].values)
        bt = fr.backtest_sensitivity_for(f, base, np.random.default_rng(0), 200, replicates=3, batch=70, seed=9)
        self.assertEqual(bt["monthend_snapshot_100pct"]["replicates"], 3)
        self.assertIn("seed_spread_median", bt["monthend_snapshot_100pct"])
        reps = [bt[f"monthend_snapshot_100pct_rep{i}"] for i in (1, 2, 3)]
        self.assertGreater(len({(r["final_value_p5"], r["pick_change_rate"]) for r in reps}), 1)


if __name__ == "__main__":
    unittest.main()

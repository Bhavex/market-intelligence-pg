"""Cost, turnover and benchmark arithmetic of src/backtest_revision.py (no network, no database)."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import backtest_revision as bt  # noqa: E402


def months(n):
    return pd.period_range("2020-01", periods=n, freq="M")


class Costs(unittest.TestCase):
    def test_cost_is_taken_off_the_start_of_month_value(self):
        net = bt.net_returns(pd.Series([0.10]), pd.Series([2.0]), 10)       # 10 bp on 200% traded = 20 bp
        self.assertAlmostEqual(net.iloc[0], (1 - 0.002) * 1.10 - 1)

    def test_zero_cost_is_the_gross_return(self):
        g = pd.Series([0.05, -0.02])
        pd.testing.assert_series_equal(bt.net_returns(g, pd.Series([2.0, 0.0]), 0), g)


class Series(unittest.TestCase):
    def setUp(self):
        idx = months(3)
        self.monthly = pd.DataFrame({"A": [0.10, 0.00, 0.05], "B": [0.00, 0.10, 0.05]}, index=idx)

    def test_rotation_trades_everything_on_a_switch_and_nothing_otherwise(self):
        pick = pd.Series(["A", "A", "B"], index=self.monthly.index)
        out = bt.series_gross_and_traded(self.monthly, pick)
        gross, traded = out["Rotation"]
        self.assertEqual(list(traded), [1.0, 0.0, 2.0])                    # entry, hold, sell 100% + buy 100%
        self.assertEqual(list(gross), [0.10, 0.00, 0.05])

    def test_rebalanced_benchmark_pays_for_drift_only(self):
        pick = pd.Series(["A"] * 3, index=self.monthly.index)
        gross, traded = bt.series_gross_and_traded(self.monthly, pick)["Equal-weight, monthly rebalanced"]
        self.assertAlmostEqual(traded.iloc[0], 1.0)
        # after month 1 (A +10%, B 0%): weights 0.5*1.10/1.05 = 0.5238 and 0.4762; back to 0.5 / 0.5 trades 2 * 0.0238
        self.assertAlmostEqual(traded.iloc[1], 2 * (0.55 / 1.05 - 0.5), places=9)
        self.assertAlmostEqual(gross.iloc[0], 0.05)

    def test_equal_returns_mean_no_drift_trading(self):
        flat = pd.DataFrame({"A": [0.03] * 3, "B": [0.03] * 3}, index=months(3))
        _, traded = bt.series_gross_and_traded(flat, pd.Series(["A"] * 3, index=flat.index))["Equal-weight, monthly rebalanced"]
        self.assertEqual(list(traded.round(12)), [1.0, 0.0, 0.0])

    def test_buy_and_hold_never_trades_after_entry_and_compounds_each_sector(self):
        pick = pd.Series(["A"] * 3, index=self.monthly.index)
        gross, traded = bt.series_gross_and_traded(self.monthly, pick)["Equal-weight buy-and-hold (5 sectors)"]
        self.assertEqual(list(traded), [1.0, 0.0, 0.0])
        end = ((1 + self.monthly).cumprod().iloc[-1]).mean()               # (1.155 + 1.155) / 2
        self.assertAlmostEqual((1 + gross).prod(), end)


class Stats(unittest.TestCase):
    def test_drawdown_counts_the_starting_capital(self):
        s = bt.stats(pd.Series([-0.10, 0.20]), pd.Series([1.0, 0.0]))
        self.assertAlmostEqual(s["max_drawdown"], -0.10)                   # a fall in month 1 is a drawdown

    def test_turnover_is_one_way_per_year(self):
        s = bt.stats(pd.Series([0.01] * 12), pd.Series([1.0] + [2.0] * 11))
        self.assertAlmostEqual(s["turnover_per_year"], (1 + 22) / 2)
        self.assertAlmostEqual(s["months_traded"], 1.0)


class Overrides(unittest.TestCase):
    def test_replaces_close_and_adj_close_and_insists_on_exactly_one_row(self):
        p = pd.DataFrame({"ticker": ["X.NS", "X.NS"], "sector": ["IT", "IT"],
                          "price_date": pd.to_datetime(["2026-10-12", "2026-10-13"]),
                          "close": [10.0, 11.0], "adj_close": [10.0, 11.0]})
        q = bt.with_overrides(p, {"X.NS": 9.0})
        self.assertEqual(list(q["close"]), [10.0, 9.0])
        self.assertEqual(list(q["adj_close"]), [10.0, 9.0])
        self.assertEqual(p["close"].iloc[1], 11.0)                         # input untouched
        with self.assertRaises(AssertionError):
            bt.with_overrides(p, {"Y.NS": 1.0})

    def test_scales_adj_close_when_a_later_dividend_has_rescaled_it(self):
        # adj_close/close = 0.99 on the day (a later dividend): the stale close must keep that factor, not drop it
        p = pd.DataFrame({"ticker": ["X.NS", "X.NS"], "sector": ["IT", "IT"],
                          "price_date": pd.to_datetime(["2026-10-12", "2026-10-13"]),
                          "close": [10.0, 11.0], "adj_close": [9.9, 10.89]})
        q = bt.with_overrides(p, {"X.NS": 10.0})
        self.assertEqual(q["close"].iloc[1], 10.0)
        self.assertAlmostEqual(q["adj_close"].iloc[1], 9.9)
        self.assertAlmostEqual(q["adj_close"].iloc[1] / q["close"].iloc[1], 0.99)
        self.assertEqual(q["adj_close"].iloc[0], 9.9)                       # other days untouched


class SectorSignals(unittest.TestCase):
    def test_return_and_momentum_use_rows_and_average_across_stocks(self):
        days = pd.bdate_range("2026-01-01", periods=22)
        rows = []
        for t, mult in (("A.NS", 1.01), ("B.NS", 1.02)):
            px = 100 * mult ** np.arange(22)
            rows += [(t, "S", d, p, p) for d, p in zip(days, px)]
        p = pd.DataFrame(rows, columns=["ticker", "sector", "price_date", "close", "adj_close"])
        ret, mom = bt.sector_daily(p)
        self.assertAlmostEqual(ret["S"].iloc[-1], (0.01 + 0.02) / 2, places=6)
        self.assertTrue(np.isnan(mom["S"].iloc[19]))                       # 20 prior rows needed
        self.assertAlmostEqual(mom["S"].iloc[20], (1.01 ** 20 + 1.02 ** 20) / 2 - 1, places=5)


if __name__ == "__main__":
    unittest.main()

"""Tests for the site export: only derived data may be published. Run:  python -m unittest discover -s tests -v
No database needed."""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import export_site  # noqa: E402


def hist():
    d = [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 5)]
    a = pd.DataFrame({"ticker": "AAA.NS", "price_date": d, "close": [200.0, 210.0, 190.0],
                      "sma_50": [200.0, 205.0, 200.0], "sma_200": [None, None, 180.0],
                      "rsi_14": [50.0, 60.0, 40.0], "volume": [1000, 2000, 3000], "is_anomaly": [False, True, False]})
    b = a.assign(ticker="BBB.NS", close=[10.0, 11.0, 12.0], sma_50=[10.0, 10.5, 11.0], sma_200=[None, None, 10.0])
    return pd.concat([a, b], ignore_index=True)


class IndexSeries(unittest.TestCase):
    def test_starts_at_100_and_keeps_shape(self):
        s, level = export_site.index_series(hist())
        self.assertEqual(s["AAA.NS"]["c"], [100.0, 105.0, 95.0])
        self.assertEqual(s["BBB.NS"]["c"], [100.0, 110.0, 120.0])
        self.assertEqual(level, {"AAA.NS": 95.0, "BBB.NS": 120.0})

    def test_sma_uses_the_same_base_so_crossovers_are_unchanged(self):
        s, _ = export_site.index_series(hist())
        self.assertEqual(s["AAA.NS"]["s50"], [100.0, 102.5, 100.0])
        self.assertEqual(s["AAA.NS"]["s200"], [None, None, 90.0])
        raw = hist().query("ticker == 'AAA.NS'")
        above_raw = (raw["close"] > raw["sma_50"]).tolist()
        above_idx = [c > m for c, m in zip(s["AAA.NS"]["c"], s["AAA.NS"]["s50"])]
        self.assertEqual(above_raw, above_idx)

    def test_signals_pass_through_and_no_raw_fields(self):
        s, _ = export_site.index_series(hist())
        self.assertEqual(s["AAA.NS"]["rsi"], [50.0, 60.0, 40.0])
        self.assertEqual(s["AAA.NS"]["a"], [1])
        self.assertEqual(set(s["AAA.NS"]), {"d", "c", "s50", "s200", "rsi", "a"})
        text = json.dumps(s)
        for raw in ("200.0", "210.0", "190.0", "1000", "2000"):
            self.assertNotIn(raw, text)

    def test_stock_without_prices_is_skipped(self):
        h = hist()
        h.loc[h.ticker == "BBB.NS", "close"] = None
        s, _ = export_site.index_series(h)
        self.assertNotIn("BBB.NS", s)


class Guard(unittest.TestCase):
    def test_rejects_raw_fields_at_any_depth(self):
        for bad in ({"close": 1}, {"a": [{"volume": 3}]}, {"x": {"y": {"official_close": 1}}}, [{"old_close": 5}]):
            with self.assertRaises(ValueError):
                export_site.assert_derived_only(bad, "t")

    def test_accepts_derived_fields(self):
        export_site.assert_derived_only({"latest": [{"ticker": "A", "index_level": 95.0, "return_1d": 0.01}],
                                         "series": {"A": {"c": [100.0], "s50": [100.0]}},
                                         "example": [{"close_change_bp": 486.36}]}, "t")

    def test_strip_trust_removes_price_columns(self):
        t = {"example": [{"ticker": "HCLTECH.NS", "old_close": 1264.5, "new_close": 1326.0, "close_change_bp": 486.36}],
             "disagreements": [{"ticker": "X", "yahoo_close": 1.0, "official_close": 2.0, "official_last_price": 3.0, "diff_bp": 5}]}
        out = export_site.strip_trust(t)
        export_site.assert_derived_only(out, "trust")
        self.assertEqual(out["example"][0]["close_change_bp"], 486.36)
        self.assertEqual(out["disagreements"][0], {"ticker": "X", "diff_bp": 5})

    def test_write_refuses_raw_field(self):
        with self.assertRaises(ValueError):
            export_site.write("never_written.json", {"latest": [{"close": 1.0}]})
        self.assertFalse((export_site.OUT / "never_written.json").exists())


if __name__ == "__main__":
    unittest.main()

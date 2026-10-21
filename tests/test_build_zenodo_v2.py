"""The Zenodo pack builder on a tiny fake study artifact (no network, no database)."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import build_zenodo_v2 as b  # noqa: E402


def fake_artifact(root: Path, spread=0.5):
    so = root / "study_output"
    (so / "dataset").mkdir(parents=True)
    (so / "dataset" / "universe.csv").write_text("ticker\nTCS.NS\n")
    (so / "dataset" / "DATASHEET.md").write_text("# datasheet\n")
    rows = [("dashboard", "intraday_snapshot", s, 100, 5, 0.05, 0.04, 0.06, 0.03, 0.07) for s in b.SIGNALS]
    pd.DataFrame(rows, columns=["universe", "scenario", "signal", "n_compared", "n_flipped", "flip_rate", "ci_low",
                                "ci_high", "boot_low", "boot_high"]).to_csv(so / "fragility_flip_rates.csv", index=False)
    bt = [("baseline", "final_value_rs", 458.72), ("baseline", "months", 142)]
    for sc in b.BACKTEST_SETTINGS:
        bt += [(sc, m, v) for m, v in [("draws_per_replicate", 1000), ("replicates", 3), ("final_value_median", 400.0),
                                       ("final_value_p5", 290.0), ("final_value_p95", 530.0), ("seed_spread_median", spread),
                                       ("seed_spread_p5", spread), ("seed_spread_p95", spread)]]
    pd.DataFrame(bt, columns=["scenario", "metric", "value"]).to_csv(so / "backtest_sensitivity.csv", index=False)
    (so / "run_parameters.json").write_text(json.dumps({
        "frag_run_id": 9, "data_through": "2026-10-19", "n_stock_days": 211859,
        "params": {"sims": 200, "seed": 42, "cutoff": "2026-10-19", "bt_draws": 1000, "bt_replicates": 3},
        "validation": {"trend_match_pct": 100.0}}))


class Pack(unittest.TestCase):
    def run_builder(self, spread):
        tmp = Path(tempfile.mkdtemp())
        fake_artifact(tmp / "a", spread)
        subprocess.run([sys.executable, str(ROOT / "src" / "build_zenodo_v2.py"), "--artifact", str(tmp / "a"),
                        "--run-url", "https://example/run/1", "--out", str(tmp / "out")], check=True, capture_output=True)
        return tmp / "out"

    def test_pack_has_the_derived_files_a_readme_and_checksums(self):
        out = self.run_builder(0.5)
        names = {p.name for p in out.iterdir()}
        self.assertTrue({"README.md", "ZENODO_DESCRIPTION.md", "SHA256SUMS", "universe.csv", "backtest_seed_stability.csv",
                         "nse_delivery_cost_schedule.csv", "backtest_costs_19oct.csv", "DATASHEET.md"} <= names)
        self.assertIn("2026-10-19", (out / "README.md").read_text())
        self.assertIn("211,859", (out / "README.md").read_text())
        self.assertIn("largest spread over all three settings Rs 0.50", (out / "ZENODO_DESCRIPTION.md").read_text())

    def test_a_missed_stability_target_is_said_plainly(self):
        out = self.run_builder(3.0)
        self.assertIn("NOT MET", (out / "ZENODO_DESCRIPTION.md").read_text())

    def test_cost_schedule_totals_the_one_way_cost(self):
        t = b.cost_schedule()
        self.assertAlmostEqual((t.iloc[-1].buy_bp + t.iloc[-1].sell_bp) / 2, 11.23, places=2)


if __name__ == "__main__":
    unittest.main()

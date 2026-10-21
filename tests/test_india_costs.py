"""NSE delivery cost arithmetic (no network, no database)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import india_costs as c  # noqa: E402


class Costs(unittest.TestCase):
    def test_buy_pays_stamp_duty_and_sell_does_not(self):
        self.assertAlmostEqual(c.side_bp(True) - c.side_bp(False), 1.5)

    def test_stt_is_ten_bp_on_each_side(self):
        # statutory and exchange charges beyond STT are small: 0.307 + 0.1 + 18% GST on those two
        self.assertAlmostEqual(c.side_bp(False), 10 + 0.407 * 1.18, places=9)

    def test_one_way_cost_is_the_buy_sell_average(self):
        self.assertAlmostEqual(c.one_way_bp(), 11.23026, places=5)
        self.assertEqual(c.REALISTIC_BP, 11.2)

    def test_gst_applies_to_brokerage_but_not_to_stt(self):
        self.assertAlmostEqual(c.side_bp(False, 2.0) - c.side_bp(False), 2.0 * 1.18, places=9)

    def test_flat_fee_in_bp(self):
        self.assertAlmostEqual(c.flat_fee_bp(20, 100000), 2.0)


if __name__ == "__main__":
    unittest.main()

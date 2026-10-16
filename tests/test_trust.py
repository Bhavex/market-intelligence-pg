"""Tests for the data-trust layer (sql/08-10, official_prices.py, fragility.py).
Run:  python -m unittest discover -s tests -v

Pure tests always run. Database tests need TEST_DATABASE_URL (a throwaway database with the repo
schema applied); they run in a transaction that is rolled back and never read DATABASE_URL."""
import os
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import fragility as F  # noqa: E402
from official_prices import parse  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")

UDIFF_HEAD = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,"
              "StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,"
              "SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,"
              "Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")


def udiff_row(sym, isin, series, close, vol, last=None):
    last = close if last is None else last
    return (f"2026-10-13,2026-10-13,CM,NSE,STK,1,{isin},{sym},{series},,,,,{sym} LTD,{close},{close},"
            f"{close},{close},{last},{close},,{close},,,{vol},1,10,F1,1,,,,,")


class OfficialParse(unittest.TestCase):
    def test_picks_eq_series_and_most_traded_row(self):
        raw = "\n".join([UDIFF_HEAD,
                         udiff_row("INFY", "INE009A01021", "EQ", 1062.10, 5000, last=1063.0),
                         udiff_row("INFY", "INE009A01021", "BE", 1.0, 99999),       # other series: ignored on NSE
                         udiff_row("M&M", "INE101A01026", "EQ", 3000.5, 100),
                         udiff_row("OTHER", "INE000000000", "EQ", 5.0, 1)]).encode()
        rows = {r["ticker"]: r for r in parse("NSE", raw, [("INFY.NS", None), ("M&M.NS", None), ("TCS.NS", None)])}
        self.assertEqual(set(rows), {"INFY.NS", "M&M.NS"})                   # TCS absent from the file
        self.assertEqual(rows["INFY.NS"]["close"], 1062.10)                  # close, not last traded price
        self.assertEqual(rows["INFY.NS"]["last_price"], 1063.0)
        self.assertEqual(rows["INFY.NS"]["isin"], "INE009A01021")

    def test_bse_matches_by_isin_first(self):
        raw = "\n".join([UDIFF_HEAD.replace(",NSE,", ",BSE,"),
                         udiff_row("INFOSYS", "INE009A01021", "A", 1062.15, 700)]).encode()
        rows = parse("BSE", raw, [("INFY.NS", "INE009A01021")])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["close"], 1062.15)


class FragilityMaths(unittest.TestCase):
    @staticmethod
    def prices(n=320, seed=3):
        rng = np.random.default_rng(seed)
        rows = []
        for t, sector in [("A.NS", "IT"), ("B.NS", "Auto")]:
            px = 100.0
            for d in pd.bdate_range("2025-01-01", periods=n):
                px *= float(np.exp(rng.normal(0, 0.02)))
                rows.append(dict(ticker=t, price_date=d, open=px * 0.99, high=px * 1.01, low=px * 0.98,
                                 close=px, adj_close=px * 0.97, volume=float(rng.integers(1000, 5000)),
                                 sector_name=sector, in_dashboard=True, cap_group="large"))
        return pd.DataFrame(rows)

    def test_closed_form_equals_full_recompute(self):
        """Changing one day's price via the closed form must give exactly the signals a full
        recomputation of the perturbed series gives (the basis of every scenario)."""
        p = self.prices()
        f = F.features(p)
        k = int(np.flatnonzero((f["ticker"] == "A.NS").values)[300])     # a day with full history
        new_close = f.loc[k, "close"] * 1.045
        px_new, vol_new = f["px"].values.copy(), f["volume"].values.copy()
        px_new[k] = new_close * f.loc[k, "adj_ratio"]
        vol_new[k] = f.loc[k, "volume"] * 0.3
        quick = F.day_signals(f, px_new, vol_new)

        p2 = p.copy()
        i2 = p2.index[(p2["ticker"] == "A.NS")][300]
        p2.loc[i2, "close"] = new_close
        p2.loc[i2, "adj_close"] = new_close * f.loc[k, "adj_ratio"]
        p2.loc[i2, "volume"] = f.loc[k, "volume"] * 0.3
        f2 = F.features(p2)
        full = F.day_signals(f2, f2["px"].values, f2["volume"].values)
        for sig in ["trend", "is_anomaly", "rsi_zone", "momentum_sign", "breakout_52w", "strong_breakout"]:
            self.assertEqual(str(quick[sig][k]), str(full[sig][k]), sig)
        self.assertAlmostEqual(float(quick["rsi"][k]), float(full["rsi"][k]), places=9)
        self.assertAlmostEqual(float(quick["mom20"][k]), float(full["mom20"][k]), places=12)

    def test_no_change_means_no_flips(self):
        f = F.features(self.prices())
        base = F.day_signals(f, f["px"].values, f["volume"].values)
        res = F.compare(f, base, base, np.ones(len(f), bool))
        self.assertTrue(all(t.result()[0] == 0 and t.result()[1] > 0 for t in res.values()))

    def test_cluster_interval_is_wider_when_errors_cluster(self):
        """Same 5% flip rate: spread evenly over 100 days vs concentrated in 5 days. The clustered
        case must get a much wider interval (one bad day is one observation, not 25)."""
        n = np.full(100, 25.0)
        even = np.full(100, 1.25)
        lumpy = np.zeros(100)
        lumpy[:5] = 25.0
        _, lo1, hi1 = F.ratio_ci(even, n)
        p2, lo2, hi2 = F.ratio_ci(lumpy, n)
        self.assertAlmostEqual(p2, 0.05)
        self.assertGreater(hi2 - lo2, 5 * (hi1 - lo1))

    def test_wilson_interval(self):
        p, lo, hi = F.wilson(5, 100)
        self.assertAlmostEqual(p, 0.05)
        self.assertTrue(0.02 < lo < 0.05 < hi < 0.12)
        self.assertEqual(F.wilson(0, 0), (None, None, None))


@unittest.skipUnless(TEST_DB, "set TEST_DATABASE_URL to run database tests")
class PointInTime(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(TEST_DB)
        self.conn.execute("SELECT set_config('app.run_id', '424242', false)")

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def q(self, sql, *a):
        cur = self.conn.execute(sql, a)
        return cur.fetchall() if cur.description else []

    def test_versions_and_time_travel(self):
        t = self.q("SELECT ticker FROM companies ORDER BY ticker LIMIT 1")[0][0]
        self.q("""INSERT INTO daily_prices (ticker, price_date, close, adj_close, volume)
                  VALUES (%s, '2024-01-06', 100, 100, 10)""", t)
        # a Saturday, so no real row exists; back-date the first version so the update below is a distinct, later transaction time
        self.q("""UPDATE price_versions SET sys_from = '2024-01-06 09:39+05:30'
                  WHERE ticker = %s AND price_date = '2024-01-06'""", t)
        self.q("UPDATE daily_prices SET close = 101, adj_close = 101 WHERE ticker = %s AND price_date = '2024-01-06'", t)
        hist = self.q("SELECT close, known_to IS NULL, run_id FROM price_history(%s, '2024-01-06')", t)
        self.assertEqual([(float(c), cur, r) for c, cur, r in hist], [(100.0, False, 424242), (101.0, True, 424242)])
        then = self.q("SELECT close FROM prices_as_of('2024-01-06 10:00+05:30') WHERE ticker = %s AND price_date = '2024-01-06'", t)
        self.assertEqual(float(then[0][0]), 100.0)
        kind = self.q("SELECT revision_kind FROM v_price_revisions WHERE ticker = %s AND price_date = '2024-01-06'", t)
        self.assertEqual(kind[0][0], "intraday_as_close")      # first version stored before 16:30 IST

    def test_no_change_reload_creates_no_version(self):
        t, d = self.q("SELECT ticker, price_date FROM daily_prices ORDER BY price_date LIMIT 1")[0]
        before = self.q("SELECT COUNT(*) FROM price_versions WHERE ticker = %s AND price_date = %s", t, d)[0][0]
        self.q("UPDATE daily_prices SET close = close WHERE ticker = %s AND price_date = %s", t, d)
        after = self.q("SELECT COUNT(*) FROM price_versions WHERE ticker = %s AND price_date = %s", t, d)[0][0]
        self.assertEqual(before, after)

    def test_classifier(self):
        c = lambda *a: self.q("SELECT classify_revision(" + ",".join(["%s::numeric"] * 10 + ["%s::bigint"] * 2)
                                + ", %s::varchar, %s::timestamptz, %s::date)", *a)[0][0]
        late = "2026-10-15 12:00+05:30"
        self.assertEqual(c(100, 100, 98, 97, 1, 1, 2, 2, 1, 1, 5, 5, "yahoo_daily", late, "2026-10-13"), "dividend_adjustment")
        self.assertEqual(c(100, 50, 98, 49, 1, 1, 2, 2, 1, 1, 5, 5, "yahoo_daily", late, "2026-10-13"), "split_or_bonus_adjustment")
        self.assertEqual(c(100, 100, 98, 98, 1, 1, 2, 2, 1, 1, 5, 6, "yahoo_daily", late, "2026-10-13"), "volume_revision")
        self.assertEqual(c(100, 100.5, 98, 98.5, 1, 1, 2, 2, 1, 1, 5, 5, "yahoo_hourly", late, "2026-10-13"), "hourly_fill_replaced")
        self.assertEqual(c(100, 100.5, 98, 98.5, 1, 1, 2, 2, 1, 1, 5, 5, "yahoo_daily", late, "2026-10-13"), "price_restatement")


if __name__ == "__main__":
    unittest.main()

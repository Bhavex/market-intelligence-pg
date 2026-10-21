"""fragility.main() end to end on a fake database: reads first, computes with no connection held, stores on a fresh one."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fragility as fr  # noqa: E402
from test_fragility_backtest import synthetic  # noqa: E402


class Desc:
    def __init__(self, name):
        self.name = name


class Cursor:
    def __init__(self, conn, df=None, row=None):
        self.conn, self.df, self.row = conn, df, row
        self.description = [Desc(c) for c in df.columns] if df is not None else None

    def fetchall(self):
        return list(self.df.itertuples(index=False, name=None))

    def fetchone(self):
        return self.row

    def executemany(self, sql, rows):
        self.conn.log.append(("executemany", sql.split("(")[0].strip(), len(list(rows))))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        return self.conn.execute(sql, params)


class FakeConn:
    """Answers the study's queries from a synthetic price table; every other statement is logged."""
    instances = []

    def __init__(self, p):
        self.p, self.log, self.closed = p, [], False
        FakeConn.instances.append(self)

    def execute(self, sql, params=None):
        self.log.append(("execute", " ".join(sql.split())[:40]))
        if "FROM daily_prices p" in sql:
            return Cursor(self, self.p)
        if "FROM signals" in sql:
            return Cursor(self, pd.DataFrame(columns=["ticker", "price_date", "momentum_20d", "rsi_14", "trend", "is_anomaly",
                                                       "breakout_52w", "strong_breakout", "volume_zscore_20d"]))
        if "v_price_revisions" in sql:
            return Cursor(self, pd.DataFrame(columns=["close_change_bp", "vol_ratio"]))
        if "reference_prices" in sql or "FROM price_versions" in sql:
            return Cursor(self, pd.DataFrame(columns=["ticker", "price_date", "close", "volume"]))
        return Cursor(self, row=(7,))                       # INSERT ... RETURNING id

    def cursor(self):
        return Cursor(self, row=(7,))

    def commit(self):
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Main(unittest.TestCase):
    def test_reads_then_computes_then_stores_on_a_second_connection(self):
        p = synthetic(days=330, wick=0.05)
        p = p.assign(price_date=p["price_date"])
        FakeConn.instances.clear()
        out = Path(tempfile.mkdtemp())
        argv = ["fragility.py", "--sims", "2", "--bt-draws", "40", "--bt-replicates", "2", "--cutoff", "2024-03-01",
                "--out", str(out)]
        with mock.patch.object(fr, "get_conn", lambda: FakeConn(p)), mock.patch.object(fr, "pipeline_run",
                                                                                      wraps=fr.pipeline_run), \
                mock.patch.object(sys, "argv", argv):
            fr.main()
        reader, writer = FakeConn.instances
        self.assertTrue(all(e[0] != "executemany" for e in reader.log))        # nothing written while reading
        self.assertEqual(reader.log[-1], ("commit",))                          # the read transaction is closed
        inserts = [e for e in writer.log if e[0] == "executemany"]
        self.assertEqual(len(inserts), 2)                                      # results and backtest rows
        meta = json.loads((out / "run_parameters.json").read_text())
        self.assertEqual(meta["frag_run_id"], 7)                               # filled in after the insert
        self.assertEqual(meta["params"]["cutoff"], "2024-03-01")
        flips = pd.read_csv(out / "fragility_flip_rates.csv")
        self.assertEqual(len(flips), inserts[0][2])
        self.assertTrue((out / "backtest_sensitivity.csv").exists())

    def test_results_reach_disk_even_if_the_store_fails(self):
        p = synthetic(days=330, wick=0.05)
        conns = []

        def get_conn():
            c = FakeConn(p)
            conns.append(c)
            if len(conns) == 2:
                c.execute = mock.Mock(side_effect=RuntimeError("connection lost"))
            return c
        out = Path(tempfile.mkdtemp())
        argv = ["fragility.py", "--sims", "2", "--bt-draws", "20", "--out", str(out)]
        with mock.patch.object(fr, "get_conn", get_conn), mock.patch.object(sys, "argv", argv):
            with self.assertRaises(Exception):
                fr.main()
        self.assertTrue((out / "fragility_flip_rates.csv").exists())
        self.assertIsNone(json.loads((out / "run_parameters.json").read_text())["frag_run_id"])


if __name__ == "__main__":
    unittest.main()

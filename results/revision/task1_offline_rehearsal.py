"""Offline rehearsal of the month-end backtest draws (not the study run): python task1_offline_rehearsal.py ohlc.csv 100000:42 ...
ohlc.csv from task1_fetch_ohlc.py (public Yahoo data, raw prices: keep out of git). Arguments are draws:seed."""
import sys, time, json
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
import fragility as fr
d = pd.read_csv(sys.argv.pop(1), parse_dates=["price_date"])
d = d[d.price_date <= "2026-10-19"].rename(columns={"sector": "sector_name"})
d["in_dashboard"], d["cap_group"] = True, "large"
f = fr.features(d)
base = fr.day_signals(f, f["px"].values, f["volume"].values)
for draws, seed in [(int(a), int(b)) for a, b in (x.split(":") for x in sys.argv[1:])]:
    t = time.time()
    bt = fr.backtest_sensitivity_for(f, base, np.random.default_rng(seed), draws, replicates=3, seed=seed)
    print(draws, seed, round(time.time() - t, 1), "s")
    for k in ("baseline", "monthend_snapshot_100pct", "monthend_snapshot_25pct", "monthend_snapshot_5pct"):
        print(" ", k, json.dumps(bt[k]))

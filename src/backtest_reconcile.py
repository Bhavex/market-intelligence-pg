"""Reconcile the paper's backtest numbers (Rs 458.72 / 459) with the Task 6 rebuild (Rs 462.98) and the 13 Oct
IT year-to-date gap (-24.06 vs -24.28), offline.

    python src/backtest_reconcile.py --prices raw.csv --revisions revisions.csv [--out results/revision]

--prices:     CSV with ticker, sector, price_date, close, adj_close (raw vendor data: keep it out of git).
--revisions:  revisions.csv of the released dataset (nse-free-data-quality/), used to rebuild the 13 Oct stale closes.

1. Runs the paper's own monthly_backtest() (src/fragility.py) on the CSV truncated at different last sessions.
2. Rebuilds the 09:39 IST stale state two ways: (a) stale close written over adj_close (what Task 6 did), (b) the fresh
   adj_close scaled by stale/final, which keeps the dividend adjustment consistent. Only (b) is what production held.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest_revision as br  # noqa: E402
import fragility as fr  # noqa: E402


def paper_backtest(prices: pd.DataFrame, end: str) -> tuple[float, int]:
    """Final value of Rs 100 and number of picks, computed by the paper's own function on the truncated CSV."""
    d = prices[prices["price_date"] <= pd.Timestamp(end)].rename(columns={"sector": "sector_name"}).copy()
    for c in ("open", "high", "low"):
        d[c] = np.nan                                  # not used by the baseline backtest
    d["in_dashboard"], d["cap_group"] = True, "large"
    f = fr.features(d)
    base = fr.day_signals(f, f["px"].values, f["volume"].values)
    value, picks = fr.monthly_backtest(f, base["mom20"], f["px"].values / f["px_prev"].values - 1)
    return value, len(picks)


def stale_closes(prices: pd.DataFrame, revisions: pd.DataFrame) -> dict:
    """The closes the 09:39 IST load stored: final close / (1 + close_change_bp / 1e4), from the DB's own revisions."""
    rev = revisions[revisions["revision_kind"] == "intraday_as_close"]
    final = prices[prices["price_date"] == br.SCENARIO_DAY].set_index("ticker")["close"]
    return {r.ticker: float(final[r.ticker] / (1 + r.close_change_bp / 1e4)) for r in rev.itertuples()}


def ytd(prices: pd.DataFrame) -> pd.Series:
    ret, _ = br.sector_daily(prices)
    r = ret[ret.index >= "2026-01-01"].fillna(0.0)
    return ((1 + r).prod() - 1) * 100


def equal_weight_final(prices: pd.DataFrame) -> float:
    ret, mom = br.sector_daily(prices)
    monthly, pick = br.monthly_inputs(ret, mom)
    return float(100 * (1 + monthly.mean(axis=1)).prod())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", required=True, type=Path)
    ap.add_argument("--revisions", required=True, type=Path)
    ap.add_argument("--out", default=Path("results/revision"), type=Path)
    a = ap.parse_args()
    prices = pd.read_csv(a.prices, parse_dates=["price_date"])
    rows = []

    for end in ("2026-10-13", "2026-10-16", "2026-10-19"):
        value, picks = paper_backtest(prices, end)
        rows.append({"check": "paper_monthly_backtest", "data_through": end, "rotation_final": round(value, 2), "picks": picks})

    upto13 = prices[prices["price_date"] <= br.SCENARIO_DAY]
    stale = stale_closes(upto13, pd.read_csv(a.revisions))
    over = br.with_overrides(upto13, stale)                                   # (a) close overwrites adj_close
    scaled = upto13.copy()                                                    # (b) consistent dividend adjustment
    for t, v in stale.items():
        m = (scaled["ticker"] == t) & (scaled["price_date"] == br.SCENARIO_DAY)
        scaled.loc[m, "adj_close"] = scaled.loc[m, "adj_close"].values * v / scaled.loc[m, "close"].values
        scaled.loc[m, "close"] = v
    for name, p in (("healed", upto13), ("stale_a_close_overwrites_adj", over), ("stale_b_adj_scaled", scaled)):
        y = ytd(p).round(2)
        rows.append({"check": "ytd_13oct", "state": name, **{f"ytd_{k}": v for k, v in y.items()},
                     "equal_weight_final": round(equal_weight_final(p), 2)})

    last = upto13[upto13["price_date"] == br.SCENARIO_DAY].set_index("ticker")
    factor = (last["adj_close"] / last["close"]).round(5)
    for t, f in factor[factor < 0.99999].items():
        rows.append({"check": "adj_factor_on_13oct", "ticker": t, "adj_close_over_close": f})

    out = pd.DataFrame(rows)
    a.out.mkdir(parents=True, exist_ok=True)
    out.to_csv(a.out / "taskD_reconciliation.csv", index=False)
    print(out.to_string(index=False))


if __name__ == "__main__":
    main()

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import ml_features as mf


def _fake_prices(n=300, tickers=("AAA.NS", "BBB.NS", "CCC.NS", "DDD.NS", "EEE.NS", "FFF.NS"), seed=1):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-01", periods=n)
    frames = []
    for i, t in enumerate(tickers):
        px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
        frames.append(pd.DataFrame({
            "ticker": t, "price_date": dates,
            "open": px, "high": px * 1.01, "low": px * 0.99, "close": px, "px": px,
            "volume": rng.integers(1000, 5000, n).astype(float),
            "sector_name": "IT" if i < 4 else "Auto",
        }))
    return pd.concat(frames, ignore_index=True)


def test_direction_target_is_next_day_direction():
    prices = _fake_prices()
    df = mf.labeled(mf.build_features(prices), "direction")
    one = df[df.ticker == "AAA.NS"].reset_index(drop=True)
    src = prices[prices.ticker == "AAA.NS"].set_index("price_date")["px"]
    for _, r in one.head(20).iterrows():
        assert bool(r["target"]) == (src.loc[r["target_date"]] > r["px"])


def test_sector5_target_matches_forward_relative_return():
    prices = _fake_prices(n=200)
    full = mf.build_features(prices)
    df = mf.labeled(full, "sector5")
    r = df[df.ticker == "AAA.NS"].iloc[10]
    src = prices.set_index(["ticker", "price_date"])["px"]
    d = r["price_date"]
    dates = sorted(prices["price_date"].unique())
    d5 = dates[dates.index(d) + mf.HORIZON]
    own = src[("AAA.NS", d5)] / src[("AAA.NS", d)] - 1
    peers = [src[(t, d5)] / src[(t, d)] - 1 for t in ["BBB.NS", "CCC.NS", "DDD.NS"]]
    assert abs(r["bench_fwd_ret_5"] - np.median(peers)) < 1e-12
    assert bool(r["target"]) == (own - np.median(peers) > 0)
    assert r["target_date"] == d5


def test_small_sector_falls_back_to_market_benchmark():
    df = mf.build_features(_fake_prices(n=150))
    auto = df[df.sector_name == "Auto"].dropna(subset=["bench_fwd_ret_5"])
    day = auto.iloc[0]["price_date"]
    all_day = df[df.price_date == day]
    me = auto.iloc[0]
    others = all_day[all_day.ticker != me["ticker"]]["fwd_ret_5"]
    assert abs(me["bench_fwd_ret_5"] - others.median()) < 1e-12


def test_last_rows_have_no_label():
    df = mf.build_features(_fake_prices())
    last = df.sort_values("price_date").groupby("ticker").tail(mf.HORIZON)
    assert last["target_sector5"].isna().all()
    assert len(mf.labeled(df, "direction")) == len(df) - 6


def test_split_is_chronological_with_embargo():
    df = mf.labeled(mf.build_features(_fake_prices(n=800)), "sector5")
    train, val, test = mf.time_split(df)
    assert train["price_date"].max() < val["price_date"].min()
    assert val["price_date"].max() < test["price_date"].min()
    assert (train["target_date"] < val["price_date"].min()).all()
    assert (val["target_date"] < test["price_date"].min()).all()


def test_no_future_leakage():
    prices = _fake_prices(n=400)
    df = mf.build_features(prices)
    ok, msg = mf.leakage_check(prices, df, n=10)
    assert ok, msg


def test_rank_features_are_within_unit_interval():
    df = mf.build_features(_fake_prices())
    assert df["mom_rank_20"].between(0, 1).all()
    assert df["vol_rank_20"].between(0, 1).all()


def test_loo_median_matches_bruteforce():
    rng = np.random.default_rng(0)
    for n in range(2, 9):
        a = rng.normal(size=n)
        got = mf._loo_median(a)
        for i in range(n):
            exp = np.median(np.delete(a, i))
            assert abs(got[i] - exp) < 1e-12
    a = np.array([1.0, np.nan, 3.0, 2.0])
    got = mf._loo_median(a)
    assert np.isnan(got[1]) and abs(got[0] - 2.5) < 1e-12


def test_vol5_and_sector20_targets_exist_and_align():
    prices = _fake_prices(n=200)
    df = mf.build_features(prices)
    assert df["target_vol5"].notna().sum() > 0
    assert df["target_sector20"].notna().sum() > 0
    r = mf.labeled(df, "vol5").iloc[5]
    rets = prices[prices.ticker == r["ticker"]].set_index("price_date")["px"].pct_change()
    dates = list(rets.index)
    i = dates.index(r["price_date"])
    own = rets.iloc[i + 1:i + 1 + mf.HORIZON].std()
    assert abs(r["fwd_vol_5"] - own) < 1e-12
    assert r["target_date"] == dates[i + mf.HORIZON]
    r20 = mf.labeled(df, "sector20").iloc[5]
    j = dates.index(r20["price_date"])
    assert r20["target_date"] == dates[j + mf.LONG_HORIZON]

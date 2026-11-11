import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from db import get_conn

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
TRAIN_END = date(2022, 12, 31)
VAL_END = date(2023, 12, 31)
HORIZON = 5
LONG_HORIZON = 20
FEATURES = [
    "ret_1", "ret_5", "ret_10", "ret_20", "vol_20", "rsi_14", "macd_norm", "macd_hist",
    "vol_ratio_20", "range_pos_20", "gap_sma50", "dow",
    "sector_rel_5", "sector_rel_20", "mkt_rel_20", "mom_rank_20", "vol_rank_20",
]
TARGETS = {
    "direction": "next-day close higher than today",
    "sector5": f"beats its sector median over the next {HORIZON} sessions",
    "sector20": f"beats its sector median over the next {LONG_HORIZON} sessions",
    "vol5": f"realised volatility over the next {HORIZON} sessions above its sector median",
}
TARGET_SPEC = {
    "direction": ("next_ret", "next_date", 1),
    "sector5": ("rel_fwd_5", "fwd_date_5", HORIZON),
    "sector20": ("rel_fwd_20", "fwd_date_20", LONG_HORIZON),
    "vol5": ("rel_vol_5", "fwd_date_5", HORIZON),
}
DEFAULT_TARGET = "sector5"
MIN_HISTORY = 60
MIN_SECTOR_PEERS = 3

PRICES_SQL = """
    SELECT p.ticker, p.price_date, p.open, p.high, p.low, p.close,
           COALESCE(p.adj_close, p.close) AS px, p.volume, s.sector_name
    FROM daily_prices p
    JOIN companies c ON c.ticker = p.ticker
    JOIN sectors s ON s.sector_id = c.sector_id
    WHERE c.is_active AND (%(all)s OR c.in_dashboard)
    ORDER BY p.ticker, p.price_date
"""


def cache_path(all_stocks):
    return CACHE_DIR / ("ml_features_all.pkl" if all_stocks else "ml_features.pkl")


def load_prices(conn, all_stocks=False):
    cur = conn.execute(PRICES_SQL, {"all": all_stocks})
    df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    for col in ["open", "high", "low", "close", "px"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].fillna(0).astype(float)
    df["price_date"] = pd.to_datetime(df["price_date"])
    return df


def _rsi(px, n=14):
    diff = px.diff()
    gain = diff.clip(lower=0).rolling(n).mean()
    loss = (-diff).clip(lower=0).rolling(n).mean()
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi = rsi.where(loss != 0, 100.0)
    rsi = rsi.where(~((loss == 0) & (gain == 0)), 50.0)
    return rsi


def _ticker_features(g):
    px = g["px"]
    out = pd.DataFrame(index=g.index)
    out["ret_1"] = px.pct_change(1)
    out["ret_5"] = px.pct_change(5)
    out["ret_10"] = px.pct_change(10)
    out["ret_20"] = px.pct_change(20)
    out["vol_20"] = out["ret_1"].rolling(20).std()
    out["rsi_14"] = _rsi(px)
    ema12 = px.ewm(span=12, adjust=False).mean()
    ema26 = px.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    out["macd_norm"] = macd / px
    out["macd_hist"] = (macd - macd.ewm(span=9, adjust=False).mean()) / px
    vol_avg = g["volume"].rolling(20).mean()
    out["vol_ratio_20"] = g["volume"] / vol_avg.replace(0, np.nan)
    hi20 = g["high"].rolling(20).max()
    lo20 = g["low"].rolling(20).min()
    out["range_pos_20"] = (g["close"] - lo20) / (hi20 - lo20).replace(0, np.nan)
    out["gap_sma50"] = px / px.rolling(50).mean() - 1
    out["dow"] = g["price_date"].dt.dayofweek
    out["next_ret"] = px.shift(-1) / px - 1
    out["next_date"] = g["price_date"].shift(-1)
    out["fwd_ret_5"] = px.shift(-HORIZON) / px - 1
    out["fwd_date_5"] = g["price_date"].shift(-HORIZON)
    out["fwd_ret_20"] = px.shift(-LONG_HORIZON) / px - 1
    out["fwd_date_20"] = g["price_date"].shift(-LONG_HORIZON)
    out["fwd_vol_5"] = out["ret_1"].rolling(HORIZON).std().shift(-HORIZON)
    out["n_hist"] = np.arange(len(g)) + 1
    return out


def _loo_median(values):
    a = np.asarray(values, dtype=float)
    out = np.full(a.shape, np.nan)
    ok = ~np.isnan(a)
    v = a[ok]
    n = len(v)
    if n < 2:
        return out
    order = np.argsort(v, kind="stable")
    s = v[order]
    rank = np.empty(n, dtype=int)
    rank[order] = np.arange(n)
    m = (n - 1) // 2
    if (n - 1) % 2 == 1:
        lo = np.where(m < rank, s[m], s[m + 1])
        res = lo
    else:
        a1 = np.where(m - 1 < rank, s[m - 1], s[m])
        a2 = np.where(m < rank, s[m], s[m + 1])
        res = (a1 + a2) / 2
    out[ok] = res
    return out


def _peer_median_excluding_self(df, value_col, group_cols):
    grp = df.groupby(group_cols)[value_col]
    n_peers = grp.transform("count") - df[value_col].notna().astype(int)
    med = grp.transform(_loo_median)
    return med, n_peers


def build_features(prices):
    feats = prices.groupby("ticker", group_keys=False).apply(_ticker_features, include_groups=False)
    df = pd.concat([prices[["ticker", "price_date", "sector_name", "px"]], feats], axis=1)
    by_sec = ["sector_name", "price_date"]
    by_day = ["price_date"]
    df["sector_rel_5"] = df["ret_5"] - df.groupby(by_sec)["ret_5"].transform("mean")
    df["sector_rel_20"] = df["ret_20"] - df.groupby(by_sec)["ret_20"].transform("mean")
    df["mkt_rel_20"] = df["ret_20"] - df.groupby(by_day)["ret_20"].transform("mean")
    df["mom_rank_20"] = df.groupby(by_day)["ret_20"].rank(pct=True)
    df["vol_rank_20"] = df.groupby(by_day)["vol_20"].rank(pct=True)
    df["target_direction"] = (df["next_ret"] > 0).astype("Int64")
    df.loc[df["next_ret"].isna(), "target_direction"] = pd.NA
    for src, name in (("fwd_ret_5", "rel_fwd_5"), ("fwd_ret_20", "rel_fwd_20"), ("fwd_vol_5", "rel_vol_5")):
        sec_bench, n_peers = _peer_median_excluding_self(df, src, by_sec)
        mkt_bench, _ = _peer_median_excluding_self(df, src, by_day)
        bench = sec_bench.where(n_peers >= MIN_SECTOR_PEERS, mkt_bench)
        df["bench_" + src] = bench
        df[name] = df[src] - bench
    for target, (col, _, _) in TARGET_SPEC.items():
        if target == "direction":
            continue
        df["target_" + target] = (df[col] > 0).astype("Int64")
        df.loc[df[col].isna(), "target_" + target] = pd.NA
    df = df[df["n_hist"] >= MIN_HISTORY]
    df = df.dropna(subset=FEATURES)
    return df.reset_index(drop=True)


def target_col(target=DEFAULT_TARGET):
    if target not in TARGETS:
        raise ValueError(f"unknown target {target!r}; choose from {list(TARGETS)}")
    return f"target_{target}"


def labeled(df, target=DEFAULT_TARGET):
    col = target_col(target)
    ret_col, date_col, _ = TARGET_SPEC[target]
    out = df[df[col].notna()].copy()
    out["target"] = out[col].astype(int)
    out["target_date"] = out[date_col]
    out["target_ret"] = out[ret_col]
    return out


def horizon(target=DEFAULT_TARGET):
    return TARGET_SPEC[target][2]


def time_split(df, train_end=TRAIN_END, val_end=VAL_END, embargo=HORIZON):
    d = df["price_date"].dt.date
    train = df[d <= train_end]
    val = df[(d > train_end) & (d <= val_end)]
    test = df[d > val_end]
    if embargo:
        train = train[train["price_date"] <= train["price_date"].max() - pd.tseries.offsets.BDay(embargo)]
        val = val[val["price_date"] <= val["price_date"].max() - pd.tseries.offsets.BDay(embargo)]
    return train, val, test


def leakage_check(prices, full, n=25, seed=42):
    rng = np.random.default_rng(seed)
    lab = labeled(full, "direction")
    idx = rng.choice(len(lab), size=min(n, len(lab)), replace=False)
    worst = 0.0
    for i in idx:
        row = lab.iloc[i]
        peers = prices[prices["price_date"] <= row["price_date"]]
        recomputed = build_features(peers)
        r = recomputed[(recomputed["ticker"] == row["ticker"]) & (recomputed["price_date"] == row["price_date"])]
        if r.empty:
            return False, f"{row['ticker']} {row['price_date'].date()} missing after truncation"
        diff = np.abs(r[FEATURES].iloc[0].astype(float).values - row[FEATURES].astype(float).values)
        worst = max(worst, float(np.nanmax(diff)))
        if worst > 1e-9:
            return False, f"{row['ticker']} {row['price_date'].date()} differs by {worst:.3e}"
    return True, f"{len(idx)} samples identical after truncation (max diff {worst:.1e})"


def load_or_build(all_stocks=False, refresh=False):
    path = cache_path(all_stocks)
    if path.exists() and not refresh:
        return pd.read_pickle(path)
    with get_conn() as conn:
        prices = load_prices(conn, all_stocks)
    df = build_features(prices)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
    return df


def summary(df, target=DEFAULT_TARGET):
    lab = labeled(df, target)
    train, val, test = time_split(lab, embargo=horizon(target))
    lines = [
        f"target: {target} ({TARGETS[target]})",
        f"tickers: {df['ticker'].nunique()}   sectors: {df['sector_name'].nunique()}   rows: {len(df)}   labeled: {len(lab)}",
        f"dates: {df['price_date'].min().date()} to {df['price_date'].max().date()}",
        f"train: {len(train)} rows ({train['price_date'].min().date()} to {train['price_date'].max().date()}), positive-rate {train['target'].mean():.4f}",
        f"val:   {len(val)} rows ({val['price_date'].min().date()} to {val['price_date'].max().date()}), positive-rate {val['target'].mean():.4f}",
        f"test:  {len(test)} rows ({test['price_date'].min().date()} to {test['price_date'].max().date()}), positive-rate {test['target'].mean():.4f}",
        f"features ({len(FEATURES)}): {', '.join(FEATURES)}",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--target", default="all", choices=list(TARGETS) + ["all"])
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    with get_conn() as conn:
        prices = load_prices(conn, args.all)
    df = build_features(prices)
    path = cache_path(args.all)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_pickle(path)
    for t in (TARGETS if args.target == "all" else [args.target]):
        print(summary(df, t))
        print()
    print(f"cached: {path}")
    if args.check:
        ok, msg = leakage_check(prices, df)
        print(("leakage check PASSED: " if ok else "leakage check FAILED: ") + msg)
        if not ok:
            sys.exit(1)


if __name__ == "__main__":
    main()

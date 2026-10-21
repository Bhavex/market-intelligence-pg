"""Signal-fragility study: how often does each trading signal give a DIFFERENT answer when the
input price is one of the realistic errors a free data feed actually produces?

Method
  1. Replicate the SQL signal engine (sql/03_signals.sql) in numpy/pandas and VALIDATE the replica
     against the signals stored in the database (agreement rates are saved with the results).
  2. For every scenario, perturb the inputs of a stock-day the way a real error would, recompute
     that day's signals in closed form (only day t changes; every window ending at t is updated
     exactly), and count how often each signal flips.
  3. Backtest sensitivity: how often the monthly "hold the strongest sector" decision changes when
     the month-end data is wrong, and what that does to the final value of Rs 100.

Scenarios (all on the real price history in the database)
  intraday_snapshot   a load during market hours stores an intraday price as the close (what
                      happened on 13 Oct 2026). Price = Brownian bridge from open to close at a
                      random time f in the session, clipped to [low, high]; volume = f x volume.
                      Applied to ALL stocks of a day at the SAME time f (one run = one moment).
  early_snapshot      same, but f in [0.02, 0.10] (a run in the first half hour, like 09:39 IST).
  hourly_rebuild      a missing daily candle rebuilt from hourly bars: close error and volume ratio
                      drawn from the revisions actually observed (v_price_revisions), or a small
                      default when none exist yet.
  official_vs_yahoo   recompute with the exchange's official close/volume (reference_prices)
                      instead of Yahoo's: how much do signals depend on which "correct" source?
  calibrated_noise    everyday Yahoo-vs-official differences, bootstrapped from v_source_agreement.
  observed_first_print  the first version we actually received vs the settled value (price_versions).
  adjustment_basis    the whole history recomputed on raw close instead of dividend-adjusted close.

Usage:  python src/fragility.py [--sims 20] [--seed 42]
        python src/fragility.py --cutoff 2026-10-19 --sims 200 --bt-draws 30000 --bt-replicates 3 --out results/study
          --cutoff pins the study to the data through that date (every table is filtered, so later loads and revisions
          of later days cannot enter); --bt-replicates runs the month-end backtest as independent seeds and reports their
          spread; --out writes the derived measurements as CSV/JSON (no prices) for release.
"""
import argparse
import json
import math
from pathlib import Path
from decimal import Decimal

import numpy as np
import pandas as pd

from db import get_conn, pipeline_run

SIGNALS = ["trend", "is_anomaly", "breakout_52w", "strong_breakout", "rsi_zone", "momentum_sign", "sector_leader"]


# ---------------------------------------------------------------- data
def q(conn, sql, params=None):
    cur = conn.execute(sql, params)
    df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    for c in df.columns:
        if len(df) and df[c].map(lambda v: isinstance(v, Decimal)).any():
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def load(conn, cutoff=None):
    p = q(conn, """SELECT p.ticker, p.price_date, p.open, p.high, p.low, p.close, p.adj_close, p.volume,
                          sec.sector_name, c.in_dashboard, c.cap_group
                   FROM daily_prices p JOIN companies c ON c.ticker = p.ticker
                   JOIN sectors sec ON sec.sector_id = c.sector_id
                   WHERE (%(cutoff)s::date IS NULL OR p.price_date <= %(cutoff)s::date)
                   ORDER BY p.ticker, p.price_date""", {"cutoff": cutoff})
    s = q(conn, """SELECT ticker, signal_date AS price_date, momentum_20d, rsi_14, trend, is_anomaly,
                          breakout_52w, strong_breakout, volume_zscore_20d
                   FROM signals
                   WHERE (%(cutoff)s::date IS NULL OR signal_date <= %(cutoff)s::date)""", {"cutoff": cutoff})
    p["price_date"] = pd.to_datetime(p["price_date"])
    s["price_date"] = pd.to_datetime(s["price_date"])
    for c in ["open", "high", "low", "close", "adj_close", "volume"]:
        p[c] = pd.to_numeric(p[c], errors="coerce").astype(float)
    return p, s


# ---------------------------------------------------------------- the signal engine (replica)
def features(p: pd.DataFrame, use_raw_close: bool = False) -> pd.DataFrame:
    """Per-row inputs needed to recompute a day's signals, exactly as sql/03_signals.sql."""
    out = []
    for _, g in p.groupby("ticker", sort=False):
        g = g.sort_values("price_date").copy()
        px = g["close"] if use_raw_close else g["adj_close"].fillna(g["close"])
        adj = g["close"] if use_raw_close else g["adj_close"]          # 52-week uses adj_close (NULLs stay NULL)
        vol = g["volume"]
        diff = px - px.shift(1)
        ret = px / px.shift(1).replace(0, np.nan) - 1
        # GREATEST(NULL, 0) = 0 in PostgreSQL, so the first row contributes a 0 gain/loss
        gain, loss = diff.clip(lower=0).fillna(0), (-diff).clip(lower=0).fillna(0)
        n = np.arange(1, len(g) + 1)
        f = pd.DataFrame({
            "ticker": g["ticker"].values, "price_date": g["price_date"].values,
            "sector": g["sector_name"].values,
            "in_dashboard": g["in_dashboard"].values.astype(bool), "cap_group": g["cap_group"].values,
            "open": g["open"].values, "high": g["high"].values, "low": g["low"].values,
            "close": g["close"].values, "volume": vol.values,
            "px": px.values, "px_prev": px.shift(1).values,
            "px_20": px.shift(20).values, "px_60": px.shift(60).values,
            "adj_ratio": (px / g["close"]).values,
            "sma50": px.rolling(50, min_periods=1).mean().values, "n50": np.minimum(n, 50),
            "sma200": px.rolling(200, min_periods=1).mean().values, "n200": np.minimum(n, 200),
            "gain_sum14": gain.rolling(14, min_periods=1).sum().values,
            "loss_sum14": loss.rolling(14, min_periods=1).sum().values, "n14": np.minimum(n, 14),
            "gain_t": gain.values, "loss_t": loss.values,
            "sd_prev20": ret.shift(1).rolling(20, min_periods=2).std().values,
            "vavg_prev20": vol.shift(1).rolling(20, min_periods=1).mean().values,
            "vsd_prev20": vol.shift(1).rolling(20, min_periods=2).std().values,
            "high52": adj.shift(1).rolling(252, min_periods=1).max().values,
            "n52": adj.shift(1).rolling(252, min_periods=1).count().values,
        })
        out.append(f)
    f = pd.concat(out, ignore_index=True)
    f["elig52"] = (f["n52"] == 252) & f["px"].notna() & (f["high52"] > 0)
    return f


def day_signals(f: pd.DataFrame, px_new, vol_new) -> dict:
    """Signals of each row of f when that day's (adjusted) price is px_new and volume vol_new.
    Everything else in the windows stays as stored, so this is exact for a single-day change."""
    px_new = np.asarray(px_new, float)
    vol_new = np.asarray(vol_new, float)
    px, prev = f["px"].values, f["px_prev"].values
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = px_new / prev - 1
        mom20 = px_new / f["px_20"].values - 1
        sma50 = f["sma50"].values + (px_new - px) / f["n50"].values
        sma200 = f["sma200"].values + (px_new - px) / f["n200"].values
        sma200 = np.where(f["n200"].values == 200, sma200, np.nan)
        d_new = px_new - prev
        g_new = np.where(np.isnan(d_new), 0, np.maximum(d_new, 0))
        l_new = np.where(np.isnan(d_new), 0, np.maximum(-d_new, 0))
        n14 = f["n14"].values
        ag = (f["gain_sum14"].values - f["gain_t"].values + g_new) / n14
        al = (f["loss_sum14"].values - f["loss_t"].values + l_new) / n14
        rsi = np.where((al == 0) & (ag == 0), 50, np.where(al == 0, 100, 100 - 100 / (1 + ag / al)))
        vz = (vol_new - f["vavg_prev20"].values) / np.where(f["vsd_prev20"].values == 0, np.nan, f["vsd_prev20"].values)
        vz_stored = np.round(vz, 4)       # strong_breakout reads signals.volume_zscore_20d, stored rounded
    up = (px_new > sma50) & (sma50 > sma200)
    down = (px_new < sma50) & (sma50 < sma200)
    trend = np.where(np.isnan(sma200), "NEUTRAL", np.where(up, "UP", np.where(down, "DOWN", "NEUTRAL")))
    price_spike = np.nan_to_num(np.abs(ret) > 3 * f["sd_prev20"].values, nan=0).astype(bool) & ~np.isnan(f["sd_prev20"].values) & ~np.isnan(ret)
    vol_spike = np.nan_to_num(vz > 3, nan=0).astype(bool) & ~np.isnan(vz)
    elig = f["elig52"].values
    brk = np.where(elig, px_new > f["high52"].values, np.nan)
    strong = np.where(elig, (px_new > f["high52"].values) & (np.nan_to_num(vz_stored, nan=-1) > 2), np.nan)
    rsi_zone = np.where(rsi > 70, 1, np.where(rsi < 30, -1, 0))
    return {"trend": trend, "is_anomaly": price_spike | vol_spike, "breakout_52w": brk,
            "strong_breakout": strong, "rsi_zone": rsi_zone, "rsi": rsi,
            "momentum_sign": np.sign(mom20), "mom20": mom20}


def sector_leader(f: pd.DataFrame, mom20) -> pd.Series:
    """Strongest sector of each day = highest average 20-day momentum (v_sector_strength rank 1)."""
    d = pd.DataFrame({"price_date": f["price_date"].values, "sector": f["sector"].values, "m": mom20})
    avg = d.dropna().groupby(["price_date", "sector"])["m"].mean().reset_index()
    return avg.loc[avg.groupby("price_date")["m"].idxmax()].set_index("price_date")["sector"]


# ---------------------------------------------------------------- validation
def _finite(o):
    """Replace NaN/inf (not valid in JSONB) by None, recursively."""
    if isinstance(o, dict):
        return {k: _finite(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_finite(v) for v in o]
    if isinstance(o, float) and not math.isfinite(o):
        return None
    return o


def validate(f: pd.DataFrame, s: pd.DataFrame) -> dict:
    base = day_signals(f, f["px"].values, f["volume"].values)
    b = pd.DataFrame({"ticker": f["ticker"], "price_date": f["price_date"], "trend": base["trend"],
                      "is_anomaly": base["is_anomaly"], "breakout_52w": base["breakout_52w"],
                      "rsi": base["rsi"], "mom20": base["mom20"]})
    m = b.merge(s, on=["ticker", "price_date"], how="inner", suffixes=("", "_db"))
    out = {"rows_compared": int(len(m))}
    out["trend_match_pct"] = round(100 * float((m["trend"] == m["trend_db"]).mean()), 4)
    out["anomaly_match_pct"] = round(100 * float((m["is_anomaly"] == m["is_anomaly_db"].astype(bool)).mean()), 4)
    e = m["breakout_52w_db"].notna()
    out["breakout_rows"] = int(e.sum())
    out["breakout_match_pct"] = round(100 * float((m.loc[e, "breakout_52w"].astype(bool)
                                                   == m.loc[e, "breakout_52w_db"].astype(bool)).mean()), 4) if e.any() else None
    r = m["rsi_14"].notna()
    out["rsi_within_0.01_pct"] = round(100 * float((np.abs(m.loc[r, "rsi"] - m.loc[r, "rsi_14"]) <= 0.0051).mean()), 4)
    k = m["momentum_20d"].notna()
    out["mom20_within_1e-6_pct"] = round(100 * float((np.abs(m.loc[k, "mom20"] - m.loc[k, "momentum_20d"]) <= 1.01e-6).mean()), 4)
    return out


# ---------------------------------------------------------------- statistics
BOOT_BLOCK = 20     # trading days per block (about one month)
BOOT_REPS = 1000


def wilson(k, n, z=1.96):
    if n == 0:
        return (None, None, None)
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (p, max(0.0, c - h), min(1.0, c + h))


class Tally:
    """Flip counts per cluster. One error hits every stock of a day at once, so stock-days of the same
    day are NOT independent: the interval is computed over clusters (day x simulation), not rows."""

    def __init__(self):
        self.k, self.n, self.d = [], [], []

    def add(self, k_by_cluster, n_by_cluster, day_by_cluster):
        keep = n_by_cluster > 0
        self.k.append(k_by_cluster[keep])
        self.n.append(n_by_cluster[keep])
        self.d.append(np.asarray(day_by_cluster)[keep].astype("datetime64[D]"))

    def arrays(self):
        if not self.k:
            return np.array([]), np.array([]), np.array([], "datetime64[D]")
        return np.concatenate(self.k), np.concatenate(self.n), np.concatenate(self.d)

    def result(self):
        k, n, _ = self.arrays()
        return int(k.sum()), int(n.sum()), ratio_ci(k, n)

    def bootstrap(self, rng, block=BOOT_BLOCK, reps=BOOT_REPS):
        return block_bootstrap(*self.arrays(), rng, block, reps)


def block_bootstrap(k, n, d, rng, block, reps):
    """Robustness check for ratio_ci: a moving-block bootstrap over trading days. All clusters of a day
    (every simulation) are resampled together, in runs of `block` consecutive trading days, so both the
    same-day dependence and any dependence between nearby days are kept. Returns the 2.5/97.5 percentiles
    of sum(k)/sum(n), or (None, None) when there are too few days."""
    if len(n) == 0 or k.sum() == 0 or k.sum() == n.sum():
        return (None, None)      # nothing (or everything) flips: resampling cannot vary; ratio_ci's Wilson bound stands
    days, inv = np.unique(d, return_inverse=True)
    kd = np.bincount(inv, weights=k, minlength=len(days))
    nd = np.bincount(inv, weights=n, minlength=len(days))
    n_days = len(days)
    if n_days < 2 * block:
        return (None, None)
    n_blocks = int(math.ceil(n_days / block))
    starts = rng.integers(0, n_days - block + 1, size=(reps, n_blocks))
    pick = (starts[:, :, None] + np.arange(block)).reshape(reps, -1)
    num, den = kd[pick].sum(axis=1), nd[pick].sum(axis=1)
    ratio = num / np.where(den > 0, den, np.nan)
    lo, hi = np.nanpercentile(ratio, [2.5, 97.5])
    return (float(lo), float(hi))


def ratio_ci(k, n, z=1.96):
    """95% interval for sum(k)/sum(n) with clustered data (ratio-estimator / cluster-robust standard
    error). Falls back to a Wilson interval on the number of clusters when no or all rows flip."""
    m, K, N = len(n), float(np.sum(k)), float(np.sum(n))
    if m == 0 or N == 0:
        return (None, None, None)
    p = K / N
    if K == 0 or K == N or m < 2:
        _, lo, hi = wilson(round(p * m), m)
        return (p, min(lo, p), max(hi, p))
    se = math.sqrt(m / (m - 1) * float(np.sum((k - p * n) ** 2))) / N
    return (p, max(0.0, p - z * se), min(1.0, p + z * se))


def compare(f, base, pert, idx, leader_base=None, leader_pert=None, cluster=None, tallies=None) -> dict:
    """Flip counts per signal over rows idx (boolean mask). cluster = an integer id per row (the
    contaminated day); results are added to `tallies` (dict of Tally) and also returned as totals."""
    tallies = tallies if tallies is not None else {}
    idx = np.asarray(idx, bool)
    cl = (np.asarray(cluster) if cluster is not None else
          pd.factorize(f["price_date"].values)[0])[idx]
    width = int(cl.max()) + 1 if len(cl) else 0
    day_of = np.full(width, np.datetime64("NaT"), "datetime64[D]")      # the trading day of each cluster
    day_of[cl] = f["price_date"].values[idx].astype("datetime64[D]")

    def tally(sig, flipped, ok):
        k = np.bincount(cl[ok], weights=flipped[ok].astype(float), minlength=width)
        n = np.bincount(cl[ok], minlength=width).astype(float)
        tallies.setdefault(sig, Tally()).add(k, n, day_of)

    for sig in ["trend", "is_anomaly", "rsi_zone", "momentum_sign"]:
        a, b = base[sig][idx], pert[sig][idx]
        ok = ~pd.isna(a) & ~pd.isna(b) if a.dtype != object else np.ones(len(a), bool)
        if sig == "momentum_sign":
            ok &= ~np.isnan(a.astype(float)) & ~np.isnan(b.astype(float))
        tally(sig, a != b, ok)
    for sig in ["breakout_52w", "strong_breakout"]:
        a, b = base[sig][idx].astype(float), pert[sig][idx].astype(float)
        ok = ~np.isnan(a) & ~np.isnan(b)
        tally(sig, a != b, ok)
    if leader_base is not None:
        common = leader_base.index.intersection(leader_pert.index)
        flips = (leader_base[common] != leader_pert[common]).values.astype(float)
        tallies.setdefault("sector_leader", Tally()).add(flips, np.ones(len(flips)),    # one day = one cluster
                                                         pd.to_datetime(common).values)
    return tallies


# ---------------------------------------------------------------- scenarios
def bridge_core(o, c, h, l, frac, z):
    """Brownian bridge open -> close at session fraction `frac` (z = standard normal draws), clipped to [low, high]."""
    o = np.where(np.isnan(o), c, o)
    h = np.where(np.isnan(h), np.maximum(o, c), h)
    l = np.where(np.isnan(l), np.minimum(o, c), l)
    sigma = (h - l) / 2.5
    raw = o + frac * (c - o) + np.sqrt(frac * (1 - frac)) * sigma * z
    return np.clip(raw, l, h)


def bridge_price(f, rng, frac):
    """Intraday price at fraction frac of the session: Brownian bridge open -> close within [low, high]."""
    return bridge_core(f["open"].values, f["close"].values, f["high"].values, f["low"].values,
                       frac, rng.standard_normal(len(f)))


def run_day_scenario(f, base, leader_base, eligible_days, rng, sims, make_inputs):
    """Contaminate whole days (every stock of the day), sims times. Cluster = (simulation, day)."""
    tallies = {}
    days = f["price_date"].values
    in_days = np.isin(days, eligible_days)
    day_id = pd.factorize(days)[0]
    n_days = int(day_id.max()) + 1
    lb = leader_base[leader_base.index.isin(eligible_days)]
    for sim in range(sims):
        px_new, vol_new = make_inputs(rng)
        px_new = np.where(in_days, px_new, f["px"].values)
        vol_new = np.where(in_days, vol_new, f["volume"].values)
        pert = day_signals(f, px_new, vol_new)
        lp = sector_leader(f, pert["mom20"])
        compare(f, base, pert, in_days, lb, lp[lp.index.isin(eligible_days)],
                cluster=day_id + sim * n_days, tallies=tallies)
    return tallies


def monthly_backtest(f, mom20_by_row, daily_ret_by_row):
    """Hold last month-end's strongest sector for the next month vs equal weight (Person B, task 5).
    Returns (final value of Rs 100 strategy, list of picks by hold-month)."""
    d = pd.DataFrame({"price_date": f["price_date"].values, "sector": f["sector"].values,
                      "m": mom20_by_row, "r": daily_ret_by_row})
    sec_day = d.groupby(["price_date", "sector"]).agg(m=("m", "mean"), r=("r", "mean")).reset_index()
    sec_day["month"] = sec_day["price_date"].dt.to_period("M")
    mret = sec_day.dropna(subset=["r"]).groupby(["month", "sector"])["r"].apply(lambda x: float(np.exp(np.log1p(x).sum()) - 1))
    sm = sec_day.dropna(subset=["m"])
    last_day = sm.groupby("month")["price_date"].transform("max")
    at_end = sm[sm["price_date"] == last_day]
    best = at_end.loc[at_end.groupby("month")["m"].idxmax()]
    picks = {month + 1: sector for month, sector in zip(best["month"], best["sector"])}
    value = 100.0
    for month, sector in sorted(picks.items()):
        if (month, sector) in mret.index:
            value *= 1 + mret[(month, sector)]
    return value, picks


GROUPS = {
    "dashboard": lambda f: f["in_dashboard"].values,           # the 25 stocks on the site (5 sectors)
    "large": lambda f: (f["cap_group"] == "large").values,     # dashboard + NIFTY 50
    "small": lambda f: (f["cap_group"] == "small").values,     # 25 NIFTY SMALLCAP 100 stocks
}


def subset(d: dict, mask) -> dict:
    return {k: v[mask] for k, v in d.items()}


def run_scenarios(conn, f, fr, base, base_raw, leader_base, usable, rng, sims, ref, first, hourly_model):
    """All flip scenarios for one group of stocks (f/fr = its rows; leader_base None = no sector signal)."""
    results, params = {}, {}
    lead = leader_base[leader_base.index.isin(usable)] if leader_base is not None else None

    def day_scenario(make):
        if lead is None:                                   # no sector signal for this group
            return run_day_scenario_nolead(f, base, usable, rng, sims, make)
        return run_day_scenario(f, base, leader_base, usable, rng, sims, make)

    day_id = pd.factorize(f["price_date"].values)[0]
    for name, lo, hi in [("intraday_snapshot", 0.05, 0.95), ("early_snapshot", 0.02, 0.10)]:
        def make(rng, lo=lo, hi=hi):
            frac = rng.uniform(lo, hi, int(day_id.max()) + 1)[day_id]       # one run = one moment for all stocks
            return bridge_price(f, rng, frac) * f["adj_ratio"].values, f["volume"].values * frac
        results[name] = day_scenario(make)
        params[name] = {"session_fraction": [lo, hi], "days": int(len(usable))}

    errs, ratios, params["hourly_rebuild"] = hourly_model

    def make_hourly(rng):
        return f["px"].values * (1 - rng.choice(errs, len(f))), f["volume"].values * rng.choice(ratios, len(f))
    results["hourly_rebuild"] = day_scenario(make_hourly)

    if len(ref):
        m = f[["ticker", "price_date"]].merge(ref, on=["ticker", "price_date"], how="left")
        ratio = f["close"].values / m["close"].values
        # skip days before a split/bonus: Yahoo's history is rescaled, the exchange's is not
        has = (m["close"].notna().values & np.isin(f["price_date"].values, usable) & (ratio > 0.8) & (ratio < 1.25))
        if has.any():
            px_new = np.where(has, m["close"].values * f["adj_ratio"].values, f["px"].values)
            vol_new = np.where(has & m["volume"].notna().values, m["volume"].values, f["volume"].values)
            results["official_vs_yahoo"] = compare(f, base, day_signals(f, px_new, vol_new), has)
            params["official_vs_yahoo"] = {"stock_days": int(has.sum())}
            diffs = (f["px"].values[has] / px_new[has]) - 1
            diffs = diffs[np.isfinite(diffs)]
            if len(diffs) >= 25:
                def make_noise(rng):
                    return f["px"].values * (1 + rng.choice(diffs, len(f))), f["volume"].values
                results["calibrated_noise"] = day_scenario(make_noise)
                params["calibrated_noise"] = {"n_differences": int(len(diffs)),
                                              "share_nonzero": float(np.mean(np.abs(diffs) > 1e-4))}

    if len(first):
        m = f[["ticker", "price_date"]].merge(first, on=["ticker", "price_date"], how="left")
        has = m["close"].notna().values
        if has.any():
            px_new = np.where(has, pd.to_numeric(m["close"]).values * f["adj_ratio"].values, f["px"].values)
            vol_new = np.where(has, pd.to_numeric(m["volume"]).fillna(0).values, f["volume"].values)
            results["observed_first_print"] = compare(f, base, day_signals(f, px_new, vol_new), has)
            params["observed_first_print"] = {"stock_days": int(has.sum())}

    mask = np.isin(f["price_date"].values, usable)
    lb = lead if lead is not None else None
    lr = sector_leader(fr, base_raw["mom20"]).pipe(lambda x: x[x.index.isin(usable)]) if lead is not None else None
    results["adjustment_basis"] = compare(f, base, base_raw, mask, lb, lr)
    return results, params


def run_day_scenario_nolead(f, base, eligible_days, rng, sims, make_inputs):
    """run_day_scenario without the sector signal (groups whose 'sectors' are many small industries)."""
    tallies = {}
    days = f["price_date"].values
    in_days = np.isin(days, eligible_days)
    day_id = pd.factorize(days)[0]
    n_days = int(day_id.max()) + 1
    for sim in range(sims):
        px_new, vol_new = make_inputs(rng)
        pert = day_signals(f, np.where(in_days, px_new, f["px"].values), np.where(in_days, vol_new, f["volume"].values))
        compare(f, base, pert, in_days, cluster=day_id + sim * n_days, tallies=tallies)
    return tallies


class MonthEndBacktest:
    """The monthly 'hold the strongest sector' backtest, restated so that thousands of wrong-month-end draws are cheap.

    A wrong price on a month-end changes only that day's 20-day momentum, so a draw needs the momentum of the
    decision rows (the last day of each month) and nothing else; the sector returns of the held months do not
    change. values() reproduces monthly_backtest() exactly for the same momenta (tests/test_fragility_backtest.py)."""

    def __init__(self, f, mom20_base, ret):
        d = pd.DataFrame({"price_date": f["price_date"].values, "sector": f["sector"].values,
                          "m": mom20_base, "r": ret, "row": np.arange(len(f))})
        d["month"] = d["price_date"].dt.to_period("M")
        self.sectors = sorted(d["sector"].unique())
        sidx = {s: i for i, s in enumerate(self.sectors)}
        # held-month returns, as in monthly_backtest: mean over stocks per day, compounded over the month
        sec_day = d.groupby(["price_date", "sector"]).agg(m=("m", "mean"), r=("r", "mean")).reset_index()
        sec_day["month"] = sec_day["price_date"].dt.to_period("M")
        mret = sec_day.dropna(subset=["r"]).groupby(["month", "sector"])["r"].apply(
            lambda x: float(np.exp(np.log1p(x).sum()) - 1))
        # decision day of a month = its last day with a momentum (every sector with one that day takes part)
        sm = d.dropna(subset=["m"])
        last_day = sm.groupby("month")["price_date"].transform("max")
        dec = sm[sm["price_date"] == last_day].sort_values(["month", "sector", "row"])
        self.months = sorted(dec["month"].unique())
        midx = {m: i for i, m in enumerate(self.months)}
        self.rows = dec["row"].to_numpy()
        self.row_month = dec["month"].map(midx).to_numpy()
        self.row_date = dec["price_date"].to_numpy()
        self.n_months, n_sec = len(self.months), len(self.sectors)
        key = self.row_month * n_sec + dec["sector"].map(sidx).to_numpy()
        self.A = np.zeros((len(dec), self.n_months * n_sec))            # row -> (month, sector) cell
        self.A[np.arange(len(dec)), key] = 1.0
        self.cnt = self.A.sum(axis=0).reshape(self.n_months, n_sec)      # stocks with a momentum in the cell
        # log(1 + return) of holding sector j during the month after decision month i (0 = month missing: skipped)
        self.G = np.zeros((self.n_months, n_sec))
        for i, m in enumerate(self.months):
            for j, sec in enumerate(self.sectors):
                if (m + 1, sec) in mret.index:
                    self.G[i, j] = np.log1p(mret[(m + 1, sec)])
        self.f_rows = {k: f[k].values[self.rows] for k in ("open", "high", "low", "close", "adj_ratio", "px", "px_20", "volume")}

    def picks(self, mom):
        """mom: (draws, decision rows) momentum -> (draws, months) index of the strongest sector."""
        mom = np.atleast_2d(mom)
        ok = ~np.isnan(mom)
        total = np.where(ok, mom, 0.0) @ self.A
        mean = (total.reshape(len(mom), self.n_months, -1) / np.where(self.cnt == 0, np.nan, self.cnt))
        return np.where(np.isnan(mean), -np.inf, mean).argmax(axis=2)

    def values(self, mom):
        """Final value of Rs 100 for each draw."""
        p = self.picks(mom)
        return 100.0 * np.exp(self.G[np.arange(self.n_months), p].sum(axis=1))

    def base_mom(self):
        r = self.f_rows
        return r["px"] / r["px_20"] - 1

    def draw_mom(self, rng, n, q_rate, lo=0.05, hi=0.95):
        """n draws of the decision rows' momentum when each month-end is, with probability q_rate, an intraday snapshot
        (Brownian bridge to a random moment of the session; all stocks of that month-end share the moment)."""
        r = self.f_rows
        frac = rng.uniform(lo, hi, (n, self.n_months))[:, self.row_month]
        z = rng.standard_normal((n, len(self.rows)))
        px = bridge_core(r["open"], r["close"], r["high"], r["low"], frac, z) * r["adj_ratio"]
        hit = (rng.random((n, self.n_months)) < q_rate)[:, self.row_month]
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(hit, px, r["px"]) / r["px_20"] - 1


def backtest_sensitivity_for(f, base, rng, draws, replicates=1, batch=500, seed=None):
    """Monthly 'hold the strongest sector' backtest (dashboard stocks) with wrong month-end data.
    `draws` simulated histories per setting and per replicate; the replicates are independent random streams, so the
    spread of their medians / 5th / 95th percentiles shows whether `draws` is enough (reported as seed_spread_*)."""
    bt = {}
    ret = f["px"].values / f["px_prev"].values - 1
    v0, picks0 = monthly_backtest(f, base["mom20"], ret)
    model = MonthEndBacktest(f, base["mom20"], ret)
    base_mom = model.base_mom()
    base_pick = model.picks(base_mom)[0]
    assert abs(model.values(base_mom)[0] - v0) < 1e-6 * v0, "restated backtest disagrees with monthly_backtest"
    bt["baseline"] = {"final_value_rs": round(v0, 2), "months": len(picks0)}
    streams = [rng] if replicates == 1 else [np.random.default_rng([seed if seed is not None else 0, 7919, k])
                                             for k in range(replicates)]
    for q_rate in (1.0, 0.25, 0.05):
        name = f"monthend_snapshot_{int(q_rate * 100)}pct"
        per_rep = []
        for st in streams:
            vals, changed = [], 0
            for done in range(0, draws, batch):
                n = min(batch, draws - done)
                mom = model.draw_mom(st, n, q_rate)
                vals.append(model.values(mom))
                changed += int((model.picks(mom) != base_pick).sum())
            v = np.concatenate(vals)
            per_rep.append({"pick_change_rate": changed / (draws * model.n_months),
                            "final_value_median": float(np.median(v)), "final_value_p5": float(np.percentile(v, 5)),
                            "final_value_p95": float(np.percentile(v, 95)), "draws": draws})
        pooled = {k: float(np.mean([r[k] for r in per_rep])) for k in per_rep[0]}
        bt[name] = {"pick_change_rate": round(pooled["pick_change_rate"], 6),
                    "final_value_median": round(pooled["final_value_median"], 2),
                    "final_value_p5": round(pooled["final_value_p5"], 2),
                    "final_value_p95": round(pooled["final_value_p95"], 2), "draws_per_replicate": draws,
                    "replicates": replicates}
        if replicates > 1:
            for k in ("final_value_median", "final_value_p5", "final_value_p95"):
                x = [r[k] for r in per_rep]
                bt[name][f"seed_spread_{k[12:]}"] = round(max(x) - min(x), 2)        # largest minus smallest of the replicates
            for i, r in enumerate(per_rep, 1):
                bt[f"{name}_rep{i}"] = {k: round(v, 6 if k == "pick_change_rate" else 2) for k, v in r.items()}
    return bt


def write_outputs(out: Path, frag_id, rows, bt, params, validation, through, n_stock_days):
    """Derived measurements only (flip counts, simulated backtest summaries, parameters): no prices, no volumes.
    Called once before the database insert (frag_id None) and once after it."""
    out.mkdir(parents=True, exist_ok=True)
    cols = ["universe", "scenario", "signal", "n_compared", "n_flipped", "flip_rate",
            "ci_low", "ci_high", "boot_low", "boot_high"]
    pd.DataFrame(rows, columns=cols).to_csv(out / "fragility_flip_rates.csv", index=False, float_format="%.8f")
    pd.DataFrame([(sc, k, v) for sc, d in bt.items() for k, v in d.items()],
                 columns=["scenario", "metric", "value"]).to_csv(out / "backtest_sensitivity.csv", index=False)
    (out / "run_parameters.json").write_text(json.dumps(_finite({
        "frag_run_id": None if frag_id is None else int(frag_id), "data_through": str(through),
        "n_stock_days": int(n_stock_days), "params": params, "validation": validation}), indent=2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=20, help="repetitions of each random scenario")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--cutoff", default=None, help="use only data through this date (YYYY-MM-DD)")
    ap.add_argument("--bt-draws", type=int, default=None,
                    help="simulated histories per month-end setting and replicate (default: 5 x sims)")
    ap.add_argument("--bt-replicates", type=int, default=1, help="independent random streams for the backtest draws")
    ap.add_argument("--out", type=Path, default=None, help="also write the derived measurements here (CSV/JSON)")
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)
    cutoff = args.cutoff
    bt_draws = args.bt_draws or args.sims * 5

    # Read everything first and let go of the connection: the computation below can run for an hour, and a session
    # that sits idle (or idle in a transaction) is dropped by the database (run 39545410895 lost its results that way).
    with get_conn() as conn:
        p, s = load(conn, cutoff)
        obs = q(conn, """SELECT close_change_bp, new_volume::float / NULLIF(old_volume, 0) AS vol_ratio
                         FROM v_price_revisions
                         WHERE revision_kind IN ('hourly_fill_replaced', 'price_restatement')
                           AND close_change_bp IS NOT NULL
                           AND (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date)""", {"cutoff": cutoff})
        ref = q(conn, """SELECT ticker, price_date, close, volume FROM reference_prices WHERE source = 'NSE'
                         AND (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date)""", {"cutoff": cutoff})
        first = q(conn, """SELECT ticker, price_date, close, volume FROM (
                               SELECT ticker, price_date, close, volume,
                                      ROW_NUMBER() OVER (PARTITION BY ticker, price_date ORDER BY sys_from) AS k,
                                      COUNT(*)     OVER (PARTITION BY ticker, price_date) AS n
                               FROM price_versions) v
                           WHERE k = 1 AND n > 1
                             AND (%(cutoff)s::date IS NULL OR price_date <= %(cutoff)s::date)""", {"cutoff": cutoff})
        conn.commit()

    f = features(p)
    fr = features(p, use_raw_close=True)            # same row order as f
    validation = validate(f, s)
    print("replica vs database:", validation, flush=True)
    base = day_signals(f, f["px"].values, f["volume"].values)
    base_raw = day_signals(fr, fr["px"].values, fr["volume"].values)
    params = {"sims": args.sims, "seed": args.seed, "cutoff": cutoff, "bt_draws": bt_draws,
              "bt_replicates": args.bt_replicates,
              "interval": "95% cluster-robust (cluster = contaminated day x simulation)",
              "bootstrap": {"kind": "moving-block over trading days", "block_days": BOOT_BLOCK, "reps": BOOT_REPS}}

    if len(obs) >= 10:
        errs = obs["close_change_bp"].to_numpy(float) / 1e4
        ratios = obs["vol_ratio"].dropna().to_numpy(float)
        ratios = ratios[(ratios > 0) & np.isfinite(ratios)]
        ratios = 1 / ratios if len(ratios) else np.array([0.85])
        hourly = (errs, ratios, {"calibration": "observed", "n_obs": int(len(obs))})
    else:
        hourly = (rng.normal(0, 4e-4, 1000), np.array([0.85]),
                  {"calibration": "default N(0, 4bp), volume x0.85", "n_obs": int(len(obs))})
    if len(ref):
        ref["price_date"] = pd.to_datetime(ref["price_date"])
    if len(first):
        first["price_date"] = pd.to_datetime(first["price_date"])

    results, bt = {}, {}
    for group, pick in GROUPS.items():
        mask = pick(f)
        if mask.sum() == 0:
            continue
        fg, frg = f[mask].reset_index(drop=True), fr[mask].reset_index(drop=True)
        bg, brg = subset(base, mask), subset(base_raw, mask)
        days = np.unique(fg["price_date"].values)
        usable = days[200:]                         # 200 sessions of history for every signal
        if len(usable) == 0:
            continue
        leader = sector_leader(fg, bg["mom20"]) if group == "dashboard" else None
        res, par = run_scenarios(None, fg, frg, bg, brg, leader, usable, rng, args.sims, ref, first, hourly)
        for scen, r in res.items():
            results[(group, scen)] = r
        params[group] = {"n_stocks": int(fg["ticker"].nunique()), "n_stock_days": int(len(fg)), **par}
        print(f"{group}: scenarios done", flush=True)
        if group == "dashboard":
            bt = backtest_sensitivity_for(fg, bg, rng, bt_draws, args.bt_replicates, seed=args.seed)
            print("backtest:", json.dumps(bt), flush=True)

    rows = []
    for (group, scen), res in results.items():
        for sig, t in res.items():
            k, n, (pr, lo, hi) = t.result()
            blo, bhi = t.bootstrap(rng)
            rows.append((group, scen, sig, n, k, pr, lo, hi, blo, bhi))
            print(f"{group:9} {scen:22} {sig:16} {k:>8}/{n:<8} " + (
                f"{100 * pr:7.3f}%  [{100 * lo:.3f}, {100 * hi:.3f}]" if pr is not None else "   n/a") + (
                f"  boot [{100 * blo:.3f}, {100 * bhi:.3f}]" if blo is not None else ""), flush=True)
    through = f["price_date"].max().date()
    if args.out:                                    # on disk before the database is touched again
        write_outputs(args.out, None, rows, bt, params, validation, through, len(f))

    # --- store, on a fresh connection ---
    with get_conn() as conn:
        with pipeline_run(conn, "fragility") as run:
            frag_id = conn.execute(
                """INSERT INTO fragility_runs (run_id, data_through, n_stock_days, seed, params, validation, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING frag_run_id""",
                (run["run_id"], through, int(len(f)), args.seed, json.dumps(_finite(params)), json.dumps(_finite(validation)),
                 "src/fragility.py")).fetchone()[0]
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO fragility_results (frag_run_id, universe, scenario, signal, n_compared,
                                   n_flipped, flip_rate, ci_low, ci_high, boot_low, boot_high)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", [(frag_id, *r) for r in rows])
                cur.executemany("""INSERT INTO backtest_sensitivity (frag_run_id, scenario, metric, value)
                                   VALUES (%s,%s,%s,%s)""",
                                [(frag_id, sc, k, _finite(v)) for sc, d in bt.items() for k, v in d.items()])
            conn.commit()
            print(f"stored as frag_run_id {frag_id}", flush=True)
            run["rows_upserted"] = len(rows)
            run["message"] = (f"fragility run {frag_id}: {len({g for g, _ in results})} groups, "
                              f"{len(results)} group-scenarios on {len(f)} stock-days; "
                              f"replica trend match {validation.get('trend_match_pct')}%")
    if args.out:
        write_outputs(args.out, frag_id, rows, bt, params, validation, through, len(f))

if __name__ == "__main__":
    main()

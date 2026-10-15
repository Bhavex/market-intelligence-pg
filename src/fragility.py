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
"""
import argparse
import json
import math
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


def load(conn):
    p = q(conn, """SELECT p.ticker, p.price_date, p.open, p.high, p.low, p.close, p.adj_close, p.volume,
                          sec.sector_name
                   FROM daily_prices p JOIN companies c ON c.ticker = p.ticker
                   JOIN sectors sec ON sec.sector_id = c.sector_id
                   ORDER BY p.ticker, p.price_date""")
    s = q(conn, """SELECT ticker, signal_date AS price_date, momentum_20d, rsi_14, trend, is_anomaly,
                          breakout_52w, strong_breakout, volume_zscore_20d
                   FROM signals""")
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
        self.k, self.n = [], []

    def add(self, k_by_cluster, n_by_cluster):
        keep = n_by_cluster > 0
        self.k.append(k_by_cluster[keep])
        self.n.append(n_by_cluster[keep])

    def result(self):
        k = np.concatenate(self.k) if self.k else np.array([])
        n = np.concatenate(self.n) if self.n else np.array([])
        return int(k.sum()), int(n.sum()), ratio_ci(k, n)


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

    def tally(sig, flipped, ok):
        k = np.bincount(cl[ok], weights=flipped[ok].astype(float), minlength=width)
        n = np.bincount(cl[ok], minlength=width).astype(float)
        tallies.setdefault(sig, Tally()).add(k, n)

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
        tallies.setdefault("sector_leader", Tally()).add(flips, np.ones(len(flips)))   # one day = one cluster
    return tallies


# ---------------------------------------------------------------- scenarios
def bridge_price(f, rng, frac):
    """Intraday price at fraction frac of the session: Brownian bridge open -> close within [low, high]."""
    o, c, h, l = f["open"].values, f["close"].values, f["high"].values, f["low"].values
    o = np.where(np.isnan(o), c, o)
    h = np.where(np.isnan(h), np.maximum(o, c), h)
    l = np.where(np.isnan(l), np.minimum(o, c), l)
    sigma = (h - l) / 2.5
    raw = o + frac * (c - o) + np.sqrt(frac * (1 - frac)) * sigma * rng.standard_normal(len(c))
    return np.clip(raw, l, h)


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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=20, help="repetitions of each random scenario")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    rng = np.random.default_rng(args.seed)

    with get_conn() as conn:
        with pipeline_run(conn, "fragility") as run:
            p, s = load(conn)
            f = features(p)
            validation = validate(f, s)
            print("replica vs database:", validation)
            base = day_signals(f, f["px"].values, f["volume"].values)
            leader_base = sector_leader(f, base["mom20"])
            days = np.unique(f["price_date"].values)          # datetime64 array (fast np.isin)
            usable = days[200:]                     # need 200 sessions of history for every signal
            results, params, bt = {}, {"sims": args.sims, "seed": args.seed,
                                      "interval": "95% cluster-robust (cluster = contaminated day x simulation)"}, {}

            # --- intraday snapshot (any time) and early snapshot (first half hour) ---
            for name, lo, hi in [("intraday_snapshot", 0.05, 0.95), ("early_snapshot", 0.02, 0.10)]:
                day_id = pd.factorize(f["price_date"].values)[0]

                def make(rng, lo=lo, hi=hi, day_id=day_id):
                    # one load = one moment: every stock of a day gets the same time f
                    frac = rng.uniform(lo, hi, int(day_id.max()) + 1)[day_id]
                    price = bridge_price(f, rng, frac) * f["adj_ratio"].values
                    return price, f["volume"].values * frac
                results[name] = run_day_scenario(f, base, leader_base, usable, rng, args.sims, make)
                params[name] = {"session_fraction": [lo, hi], "days": int(len(usable))}

            # --- hourly rebuild, calibrated on observed revisions when available ---
            obs = q(conn, """SELECT close_change_bp, new_volume::float / NULLIF(old_volume, 0) AS vol_ratio
                             FROM v_price_revisions
                             WHERE revision_kind IN ('hourly_fill_replaced', 'price_restatement')
                               AND close_change_bp IS NOT NULL""")
            if len(obs) >= 10:
                errs = obs["close_change_bp"].to_numpy(float) / 1e4
                ratios = obs["vol_ratio"].dropna().to_numpy(float)
                ratios = ratios[(ratios > 0) & np.isfinite(ratios)]
                ratios = 1 / ratios if len(ratios) else np.array([0.85])
                params["hourly_rebuild"] = {"calibration": "observed", "n_obs": int(len(obs))}
            else:
                errs, ratios = rng.normal(0, 4e-4, 1000), np.array([0.85])
                params["hourly_rebuild"] = {"calibration": "default N(0, 4bp), volume x0.85", "n_obs": int(len(obs))}

            def make_hourly(rng):
                e = rng.choice(errs, len(f))
                vr = rng.choice(ratios, len(f))
                return f["px"].values * (1 - e), f["volume"].values * vr
            results["hourly_rebuild"] = run_day_scenario(f, base, leader_base, usable, rng, args.sims, make_hourly)

            # --- official exchange data instead of Yahoo, and everyday discrepancies ---
            ref = q(conn, """SELECT ticker, price_date, close, volume FROM reference_prices
                             WHERE source = 'NSE'""")
            if len(ref):
                ref["price_date"] = pd.to_datetime(ref["price_date"])
                m = f[["ticker", "price_date"]].merge(ref, on=["ticker", "price_date"], how="left")
                ratio = f["close"].values / m["close"].values
                # skip days before a split/bonus: Yahoo's history is rescaled, the exchange's is not
                has = (m["close"].notna().values & np.isin(f["price_date"].values, usable)
                       & (ratio > 0.8) & (ratio < 1.25))
                px_new = np.where(has, m["close"].values * f["adj_ratio"].values, f["px"].values)
                vol_new = np.where(has & m["volume"].notna().values, m["volume"].values, f["volume"].values)
                pert = day_signals(f, px_new, vol_new)
                results["official_vs_yahoo"] = compare(f, base, pert, has)
                params["official_vs_yahoo"] = {"stock_days": int(has.sum())}
                diffs = (f["px"].values[has] / px_new[has]) - 1
                diffs = diffs[np.isfinite(diffs)]
                if len(diffs) >= 25:
                    def make_noise(rng):
                        return f["px"].values * (1 + rng.choice(diffs, len(f))), f["volume"].values
                    results["calibrated_noise"] = run_day_scenario(f, base, leader_base, usable, rng, args.sims, make_noise)
                    params["calibrated_noise"] = {"n_differences": int(len(diffs)),
                                                  "share_nonzero": float(np.mean(np.abs(diffs) > 1e-4))}

            # --- what we actually received first vs the settled value ---
            first = q(conn, """SELECT ticker, price_date, close, volume, n FROM (
                                   SELECT ticker, price_date, close, volume,
                                          ROW_NUMBER() OVER (PARTITION BY ticker, price_date ORDER BY sys_from) AS k,
                                          COUNT(*)     OVER (PARTITION BY ticker, price_date) AS n
                                   FROM price_versions) v
                               WHERE k = 1 AND n > 1""")
            first = first[first["n"] > 1]
            if len(first):
                first["price_date"] = pd.to_datetime(first["price_date"])
                m = f[["ticker", "price_date"]].merge(first, on=["ticker", "price_date"], how="left")
                has = m["close"].notna().values
                px_new = np.where(has, pd.to_numeric(m["close"]).values * f["adj_ratio"].values, f["px"].values)
                vol_new = np.where(has, pd.to_numeric(m["volume"]).fillna(0).values, f["volume"].values)
                pert = day_signals(f, px_new, vol_new)
                results["observed_first_print"] = compare(f, base, pert, has)
                params["observed_first_print"] = {"stock_days": int(has.sum())}

            # --- adjustment basis: whole history on raw close ---
            fr = features(p, use_raw_close=True)
            pert = day_signals(fr, fr["px"].values, fr["volume"].values)
            mask = np.isin(f["price_date"].values, usable)
            results["adjustment_basis"] = compare(f, base, pert, mask, leader_base[leader_base.index.isin(usable)],
                                                  sector_leader(fr, pert["mom20"]).pipe(lambda x: x[x.index.isin(usable)]))

            # --- backtest: wrong month-end data ---
            ret = f["px"].values / f["px_prev"].values - 1
            v0, picks0 = monthly_backtest(f, base["mom20"], ret)
            bt["baseline"] = {"final_value_rs": round(v0, 2), "months": len(picks0)}
            month_ends = set(f.groupby(f["price_date"].dt.to_period("M"))["price_date"].max())
            is_me = np.isin(f["price_date"].values, np.array(list(month_ends), dtype="datetime64[ns]"))
            for q_rate in (1.0, 0.25, 0.05):
                vals, changed, n_dec = [], 0, 0
                bt_day = pd.factorize(f["price_date"].values)[0]
                for _ in range(args.sims * 5):
                    frac = rng.uniform(0.05, 0.95, int(bt_day.max()) + 1)[bt_day]
                    price = bridge_price(f, rng, frac) * f["adj_ratio"].values
                    hit_days = np.array([d for d in month_ends if rng.random() < q_rate], dtype="datetime64[ns]")
                    hit = is_me & np.isin(f["price_date"].values, hit_days)
                    pert = day_signals(f, np.where(hit, price, f["px"].values),
                                       np.where(hit, f["volume"].values * frac, f["volume"].values))
                    v, picks = monthly_backtest(f, pert["mom20"], ret)
                    vals.append(v)
                    changed += sum(picks.get(k) != picks0[k] for k in picks0)
                    n_dec += len(picks0)
                key = f"monthend_snapshot_{int(q_rate * 100)}pct"
                bt[key] = {"pick_change_rate": round(changed / max(n_dec, 1), 6),
                           "final_value_median": round(float(np.median(vals)), 2),
                           "final_value_p5": round(float(np.percentile(vals, 5)), 2),
                           "final_value_p95": round(float(np.percentile(vals, 95)), 2)}

            # --- store ---
            through = f["price_date"].max().date()
            frag_id = conn.execute(
                """INSERT INTO fragility_runs (run_id, data_through, n_stock_days, seed, params, validation, notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING frag_run_id""",
                (run["run_id"], through, int(len(f)), args.seed, json.dumps(_finite(params)), json.dumps(_finite(validation)),
                 "src/fragility.py")).fetchone()[0]
            rows = []
            for scen, res in results.items():
                for sig, t in res.items():
                    k, n, (pr, lo, hi) = t.result()
                    rows.append((frag_id, scen, sig, n, k, pr, lo, hi))
                    print(f"{scen:22} {sig:16} {k:>8}/{n:<8} " + (
                        f"{100 * pr:7.3f}%  [{100 * lo:.3f}, {100 * hi:.3f}]" if pr is not None else "   n/a"))
            with conn.cursor() as cur:
                cur.executemany("""INSERT INTO fragility_results (frag_run_id, scenario, signal, n_compared,
                                   n_flipped, flip_rate, ci_low, ci_high) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", rows)
                cur.executemany("""INSERT INTO backtest_sensitivity (frag_run_id, scenario, metric, value)
                                   VALUES (%s,%s,%s,%s)""",
                                [(frag_id, sc, k, _finite(v)) for sc, d in bt.items() for k, v in d.items()])
            conn.commit()
            print("backtest:", json.dumps(bt))
            run["rows_upserted"] = len(rows)
            run["message"] = (f"fragility run {frag_id}: {len(results)} scenarios on {len(f)} stock-days; "
                              f"replica trend match {validation.get('trend_match_pct')}%")


if __name__ == "__main__":
    main()

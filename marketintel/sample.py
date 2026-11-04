from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

UNIVERSE = {
    "IT": ["TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS"],
    "Banking": ["HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS", "AXISBANK.NS"],
    "Auto": ["MARUTI.NS", "M&M.NS", "BAJAJ-AUTO.NS", "EICHERMOT.NS", "HEROMOTOCO.NS"],
    "Pharma": ["SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "LUPIN.NS"],
    "FMCG": ["HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS"],
}

SECTOR_OF = {t: s for s, ts in UNIVERSE.items() for t in ts}


@dataclass(frozen=True)
class SampleConfig:
    start: date = date(2016, 1, 4)
    end: date = date(2026, 10, 30)
    seed: int = 7
    market_drift: float = 0.11
    market_vol: float = 0.15
    sector_vol: float = 0.10
    stock_vol: float = 0.20
    sector_momentum: float = 0.0
    shock_prob: float = 0.0015
    tickers: tuple = tuple(SECTOR_OF)


def trading_days(start: date, end: date, holidays=()) -> list:
    hol = set(holidays)
    out, d = [], start
    while d <= end:
        if d.weekday() < 5 and d not in hol:
            out.append(d)
        d += timedelta(days=1)
    return out


def generate_prices(cfg: SampleConfig = SampleConfig(), holidays=()) -> pd.DataFrame:
    rng = np.random.default_rng(cfg.seed)
    days = trading_days(cfg.start, cfg.end, holidays)
    n = len(days)
    dt = 1 / 252
    mkt = rng.normal((cfg.market_drift - cfg.market_vol ** 2 / 2) * dt, cfg.market_vol * np.sqrt(dt), n)
    sectors = sorted({SECTOR_OF[t] for t in cfg.tickers})
    sec_ret = {}
    for s in sectors:
        shocks = rng.normal(0, cfg.sector_vol * np.sqrt(dt), n)
        drift = np.zeros(n)
        if cfg.sector_momentum:
            level = 0.0
            for i in range(n):
                level = 0.995 * level + cfg.sector_momentum * shocks[i]
                drift[i] = level
        sec_ret[s] = shocks + drift
    frames = []
    for i, t in enumerate(cfg.tickers):
        s = SECTOR_OF[t]
        beta = rng.uniform(0.7, 1.3)
        idio = rng.normal(0, cfg.stock_vol * np.sqrt(dt), n)
        shocks = np.where(rng.random(n) < cfg.shock_prob, rng.normal(0, 0.06, n), 0.0)
        r = beta * mkt + sec_ret[s] + idio + shocks
        start_px = float(np.exp(rng.uniform(np.log(80), np.log(9000))))
        close = start_px * np.exp(np.cumsum(r))
        gap = rng.normal(0, 0.004, n)
        open_ = np.r_[close[0], close[:-1]] * np.exp(gap)
        span = np.abs(rng.normal(0, 0.009, n)) + 0.002
        high = np.maximum(open_, close) * (1 + span * rng.uniform(0.3, 1.0, n))
        low = np.minimum(open_, close) * (1 - span * rng.uniform(0.3, 1.0, n))
        base_vol = float(np.exp(rng.uniform(np.log(4e5), np.log(2e7))))
        volume = base_vol * np.exp(rng.normal(0, 0.35, n) + 8 * np.abs(r))
        spikes = rng.random(n) < 0.004
        volume = np.where(spikes, volume * rng.uniform(3, 6, n), volume)
        frames.append(pd.DataFrame({
            "ticker": t, "sector_name": s, "price_date": days,
            "open": np.round(open_, 2), "high": np.round(high, 2), "low": np.round(low, 2),
            "close": np.round(close, 2), "adj_close": np.round(close, 4),
            "volume": volume.astype(np.int64),
        }))
    return pd.concat(frames, ignore_index=True)

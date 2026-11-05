from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import pandas as pd

BASES = ("unadjusted", "split_adjusted", "total_return")
EPS = 1e-12


@dataclass(frozen=True)
class Action:
    ticker: str
    ex_date: pd.Timestamp
    kind: str
    value: float


def split(ticker: str, ex_date, old_face_value: float, new_face_value: float) -> Action:
    return Action(ticker, pd.Timestamp(ex_date), "split", old_face_value / new_face_value)


def bonus(ticker: str, ex_date, new_shares: float, per_held: float) -> Action:
    return Action(ticker, pd.Timestamp(ex_date), "bonus", 1 + new_shares / per_held)


def dividend(ticker: str, ex_date, per_share: float) -> Action:
    return Action(ticker, pd.Timestamp(ex_date), "dividend", per_share)


@dataclass(frozen=True)
class Order:
    signal_date: pd.Timestamp
    target: pd.Series | Callable[[pd.Series], pd.Series]
    exec_date: pd.Timestamp | None = None


@dataclass
class Ledger:
    nav: pd.Series
    cash: pd.Series
    shares: pd.DataFrame
    trades: pd.DataFrame
    turnover: pd.Series
    costs: pd.Series
    dividends: pd.Series
    initial: float
    targets: dict = field(default_factory=dict)

    @property
    def returns(self) -> pd.Series:
        prev = self.nav.shift(1)
        prev.iloc[0] = self.initial
        return self.nav / prev - 1


def _actions_by_position(actions, dates: pd.DatetimeIndex, cols: list, basis: str) -> dict:
    if basis not in BASES:
        raise ValueError(f"unknown price basis {basis!r}; use one of {BASES}")
    out = {}
    for a in actions:
        if a.kind not in ("split", "bonus", "dividend"):
            raise ValueError(f"unknown corporate action {a.kind!r}")
        if a.value <= 0:
            raise ValueError(f"corporate action value must be positive: {a}")
        if basis == "total_return":
            raise ValueError("total_return prices already include splits, bonuses and dividends; pass no actions")
        if basis == "split_adjusted" and a.kind != "dividend":
            continue
        if a.ticker not in cols:
            continue
        pos = int(dates.searchsorted(a.ex_date))
        if pos < len(dates):
            out.setdefault(pos, []).append((cols.index(a.ticker), a.kind, float(a.value)))
    return out


def _orders_by_position(orders, dates: pd.DatetimeIndex, lag: int) -> dict:
    position = {d: i for i, d in enumerate(dates)}
    out = {}
    for o in sorted(orders, key=lambda o: (pd.Timestamp(o.signal_date), o.exec_date is not None)):
        if o.exec_date is not None:
            pos = int(dates.searchsorted(pd.Timestamp(o.exec_date)))
        else:
            sig = pd.Timestamp(o.signal_date)
            if sig not in position:
                raise ValueError(f"signal date {sig.date()} is not a session in the price index")
            pos = position[sig] + lag
        if pos < len(dates):
            out[pos] = o
    return out


def run(prices: pd.DataFrame, orders, cost_bp: float = 0.0, lag: int = 1, actions=(), basis: str = "unadjusted",
        initial: float = 100.0, exec_prices: pd.DataFrame | None = None, max_open_sessions: int = 5) -> Ledger:
    if lag < 0:
        raise ValueError("lag must be zero or positive")
    prices = prices.sort_index().astype(float)
    dates, cols = prices.index, list(prices.columns)
    close = prices.to_numpy()
    trade_px = close if exec_prices is None else exec_prices.reindex(index=dates, columns=cols).astype(float).to_numpy()
    acts = _actions_by_position(actions, dates, cols, basis)
    queue = _orders_by_position(orders, dates, lag)
    c = cost_bp / 1e4
    n, k = close.shape
    shares, last = np.zeros(k), np.full(k, np.nan)
    cash = float(initial)
    nav, cash_s, turn, cost_s, div_s = (np.zeros(n) for _ in range(5))
    shares_s = np.zeros((n, k))
    trades, targets = [], {}
    open_order = None

    for i in range(n):
        for j, kind, v in acts.get(i, ()):
            if kind == "dividend":
                paid = shares[j] * v
                cash += paid
                div_s[i] += paid
            else:
                shares[j] *= v
                if not np.isnan(last[j]):
                    last[j] /= v

        if i in queue:
            o = queue[i]
            open_order = {"order": o, "opened": i, "weights": None}

        if open_order is not None:
            p = trade_px[i]
            tradable = ~np.isnan(p)
            mark = np.where(tradable, p, last)
            held = np.where(shares > 0, shares * np.nan_to_num(mark), 0.0)
            nav_exec = cash + held.sum()
            if open_order["weights"] is None:
                tgt = open_order["order"].target
                if callable(tgt):
                    tgt = tgt(pd.Series(held / nav_exec if nav_exec > 0 else held, index=cols))
                w = tgt.reindex(cols).fillna(0.0).to_numpy(dtype=float)
                if (w < -EPS).any() or w.sum() > 1 + 1e-9:
                    raise ValueError("target weights must be non-negative and sum to at most 1")
                open_order["weights"] = w
                targets[pd.Timestamp(open_order["order"].signal_date)] = pd.Series(w, index=cols)
            w = open_order["weights"]
            desired = np.where(tradable, w * nav_exec / np.where(tradable, p, 1.0), shares)
            delta = desired - shares
            tol = EPS * max(nav_exec, 1.0)
            sell = tradable & (delta * np.where(tradable, p, 0.0) < -tol)
            buy = tradable & (delta * np.where(tradable, p, 0.0) > tol)
            sold = float((-delta[sell] * p[sell]).sum())
            cash += sold * (1 - c)
            wanted = float((delta[buy] * p[buy]).sum())
            scale = 1.0 if wanted <= 0 else min(1.0, (cash / (1 + c)) / wanted)
            bought = wanted * scale
            cash -= bought * (1 + c)
            if abs(cash) < tol:
                cash = 0.0
            shares[sell] += delta[sell]
            shares[buy] += delta[buy] * scale
            shares[np.abs(shares) < EPS] = 0.0
            turn[i] += (sold + bought) / nav_exec if nav_exec > 0 else 0.0
            cost_s[i] += (sold + bought) * c
            sig = pd.Timestamp(open_order["order"].signal_date)
            for j in np.flatnonzero(sell | buy):
                q = float(delta[j] * (scale if buy[j] else 1.0))
                if abs(q) <= EPS:
                    continue
                trades.append({"date": dates[i], "signal_date": sig, "ticker": cols[j], "shares": q,
                               "price": float(p[j]), "notional": q * float(p[j]), "cost": abs(q * float(p[j])) * c})
            needed = ~tradable & ((w > EPS) | (shares > 0))
            if not needed.any() or i - open_order["opened"] + 1 >= max_open_sessions:
                open_order = None

        last = np.where(np.isnan(close[i]), last, close[i])
        nav[i] = cash + float(np.where(shares > 0, shares * np.nan_to_num(last), 0.0).sum())
        cash_s[i] = cash
        shares_s[i] = shares

    tr = pd.DataFrame(trades, columns=["date", "signal_date", "ticker", "shares", "price", "notional", "cost"])
    return Ledger(pd.Series(nav, index=dates), pd.Series(cash_s, index=dates), pd.DataFrame(shares_s, index=dates, columns=cols),
                  tr, pd.Series(turn, index=dates), pd.Series(cost_s, index=dates), pd.Series(div_s, index=dates),
                  float(initial), targets)

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import portfolio
from india_costs import REALISTIC_BP

TRADING_DAYS = 252


@dataclass(frozen=True)
class BacktestConfig:
    cost_bp: float = REALISTIC_BP
    lag_days: int = 1
    momentum_lookback: int = 60
    momentum_top_n: int = 5
    sector_lookback: int = 20
    sector_top_k: int = 1
    warmup_days: int = 61


class QualityTimeline:
    def __init__(self, states: pd.DataFrame | None = None, excluded=("suspect", "rejected"),
                 decision_hour: int = 23, decision_minute: int = 59):
        cols = ["ticker", "price_date", "state", "decided_at"]
        df = pd.DataFrame(columns=cols) if states is None or len(states) == 0 else states[cols].copy()
        df["price_date"] = pd.to_datetime(df["price_date"])
        df["decided_at"] = pd.to_datetime(df["decided_at"], utc=True)
        self.states = df.sort_values("decided_at", kind="stable").reset_index(drop=True)
        self.excluded = set(excluded)
        self.decision_hour = decision_hour
        self.decision_minute = decision_minute

    def __len__(self):
        return len(self.states)

    def decision_time(self, d) -> pd.Timestamp:
        d = pd.Timestamp(d)
        return pd.Timestamp(d.year, d.month, d.day, self.decision_hour, self.decision_minute,
                            tz="Asia/Kolkata").tz_convert("UTC")

    def excluded_at(self, ts: pd.Timestamp) -> list:
        known = self.states[self.states["decided_at"] <= ts]
        if known.empty:
            return []
        latest = known.groupby(["ticker", "price_date"], sort=False).tail(1)
        bad = latest[latest["state"].isin(self.excluded)]
        return list(zip(bad["ticker"], bad["price_date"]))

    def mask(self, d, px: pd.DataFrame) -> pd.DataFrame:
        if self.states.empty:
            return px
        cells = [(t, day) for t, day in self.excluded_at(self.decision_time(d)) if t in px.columns and day in px.index]
        if not cells:
            return px
        out = px.copy()
        for t, day in cells:
            out.loc[day, t] = np.nan
        return out


class Membership:
    def __init__(self, intervals: pd.DataFrame):
        df = intervals[["ticker", "valid_from", "valid_to"]].copy()
        df["valid_from"] = pd.to_datetime(df["valid_from"])
        df["valid_to"] = pd.to_datetime(df["valid_to"])
        self.intervals = df.reset_index(drop=True)

    def members(self, d) -> set:
        d = pd.Timestamp(d)
        live = (self.intervals["valid_from"] <= d) & (self.intervals["valid_to"].isna() | (self.intervals["valid_to"] > d))
        return set(self.intervals.loc[live, "ticker"])

    def change_dates(self) -> list:
        dates = set(self.intervals["valid_from"]) | set(self.intervals["valid_to"].dropna())
        return sorted(dates)


@dataclass
class Result:
    name: str
    returns: pd.Series
    gross_returns: pd.Series
    turnover: pd.Series
    holdings: dict = field(default_factory=dict)
    ledger: object = None


def signal_dates(index: pd.DatetimeIndex) -> list:
    s = pd.Series(index, index=index)
    return list(s.groupby(index.to_period("M")).max())


def momentum_weights(px: pd.DataFrame, lookback: int, top_n: int) -> pd.Series:
    if len(px) <= lookback:
        return pd.Series(dtype=float)
    mom = px.iloc[-1] / px.iloc[-1 - lookback] - 1
    mom = mom.dropna()
    picks = mom.sort_values(ascending=False).head(top_n).index
    return pd.Series(1.0 / len(picks), index=picks) if len(picks) else pd.Series(dtype=float)


def sector_strength_weights(px: pd.DataFrame, sectors: dict, lookback: int, top_k: int) -> pd.Series:
    if len(px) <= lookback:
        return pd.Series(dtype=float)
    mom = (px.iloc[-1] / px.iloc[-1 - lookback] - 1).dropna()
    by_sector = mom.groupby(mom.index.map(sectors)).mean().sort_values(ascending=False)
    chosen = set(by_sector.head(top_k).index)
    names = [t for t in mom.index if sectors.get(t) in chosen]
    return pd.Series(1.0 / len(names), index=names) if names else pd.Series(dtype=float)


def equal_weights(px: pd.DataFrame) -> pd.Series:
    names = px.iloc[-1].dropna().index
    return pd.Series(1.0 / len(names), index=names)


def _reconstitute(membership: Membership, day: pd.Timestamp, row: pd.Series, cols: list):
    def target(current: pd.Series) -> pd.Series:
        members = membership.members(day)
        w = current.copy()
        held = set(w.index[w > 0])
        leavers = sorted(held - members)
        if not leavers:
            return w
        freed = float(w[leavers].sum())
        w[leavers] = 0.0
        joiners = [t for t in sorted(members - held) if t in cols and not np.isnan(row[t])]
        if joiners:
            w[joiners] += freed / len(joiners)
        elif w.sum() > 0:
            w = w / w.sum() * (w.sum() + freed)
        return w
    return target


def simulate(px: pd.DataFrame, weight_fn: Callable[[pd.DataFrame], pd.Series], cfg: BacktestConfig, name: str,
             rebalance: str = "monthly", timeline: QualityTimeline | None = None,
             membership: Membership | None = None, actions=(), basis: str = "total_return",
             exec_prices: pd.DataFrame | None = None) -> Result:
    px = px.sort_index()
    dates = px.index
    cols = list(px.columns)
    position = {d: i for i, d in enumerate(dates)}
    candidates = [d for d in signal_dates(dates) if position[d] >= cfg.warmup_days]
    if rebalance == "once":
        candidates = candidates[:1]
    orders = []
    for d in candidates:
        if position[d] + cfg.lag_days >= len(dates):
            continue
        known = px.iloc[: position[d] + 1]
        if membership is not None:
            known = known[[c for c in known.columns if c in membership.members(d)]]
        if timeline is not None:
            known = timeline.mask(d, known)
        target = weight_fn(known)
        if len(target):
            orders.append(portfolio.Order(d, target.reindex(cols).fillna(0.0)))
    exec_positions = {position[o.signal_date] + cfg.lag_days for o in orders}
    if membership is not None and rebalance == "once" and orders:
        first_exec = min(exec_positions)
        for c in membership.change_dates():
            pos = int(dates.searchsorted(c))
            if first_exec < pos < len(dates) and pos not in exec_positions:
                orders.append(portfolio.Order(dates[pos], _reconstitute(membership, dates[pos], px.iloc[pos], cols),
                                              exec_date=dates[pos]))
                exec_positions.add(pos)
    led = portfolio.run(px, orders, cfg.cost_bp, cfg.lag_days, actions, basis, exec_prices=exec_prices)
    free = portfolio.run(px, orders, 0.0, cfg.lag_days, actions, basis, exec_prices=exec_prices)
    holdings = {d: sorted(t.index[t > 0]) for d, t in led.targets.items()}
    first = dates[min(exec_positions)] if orders else dates[0]
    keep = dates >= first
    return Result(name, led.returns[keep], free.returns[keep], led.turnover[keep], holdings, led)


def _label(x) -> str:
    return str(x.date()) if isinstance(x, pd.Timestamp) else str(x)


def max_drawdown(returns: pd.Series) -> float:
    eq = (1 + returns).cumprod()
    return float((eq / eq.cummax() - 1).min())


def metrics(returns: pd.Series, benchmark: pd.Series | None = None, periods_per_year: int = TRADING_DAYS,
            turnover: pd.Series | None = None) -> dict:
    r = returns.dropna()
    eq = (1 + r).cumprod()
    years = len(r) / periods_per_year
    vol = float(r.std(ddof=1) * math.sqrt(periods_per_year)) if len(r) > 1 else float("nan")
    out = {
        "start": _label(r.index[0]),
        "end": _label(r.index[-1]),
        "final_value_of_100": float(100 * eq.iloc[-1]),
        "total_return": float(eq.iloc[-1] - 1),
        "cagr": float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else float("nan"),
        "volatility": vol,
        "sharpe": float(r.mean() * periods_per_year / vol) if vol and vol > 0 else float("nan"),
        "max_drawdown": max_drawdown(r),
    }
    monthly = r if periods_per_year == 12 else (1 + r).groupby(r.index.to_period("M")).prod() - 1
    out["positive_months"] = float((monthly > 0).mean())
    if benchmark is not None:
        b = benchmark.reindex(r.index).fillna(0.0)
        bm = b if periods_per_year == 12 else (1 + b).groupby(b.index.to_period("M")).prod() - 1
        bm = bm.reindex(monthly.index)
        out["hit_rate_vs_benchmark"] = float((monthly > bm).mean())
        out["excess_cagr_vs_benchmark"] = out["cagr"] - metrics(b, periods_per_year=periods_per_year)["cagr"]
    if turnover is not None:
        out["turnover_per_year"] = float(turnover.sum() / years) if years > 0 else float("nan")
    return out


def run_all(px: pd.DataFrame, sectors: dict, cfg: BacktestConfig = BacktestConfig(),
            timeline: QualityTimeline | None = None, membership: Membership | None = None) -> dict:
    kw = {"timeline": timeline, "membership": membership}
    bench = simulate(px, equal_weights, cfg, "Equal-weight buy-and-hold", rebalance="once", **kw)
    results = {
        "momentum": simulate(px, lambda p: momentum_weights(p, cfg.momentum_lookback, cfg.momentum_top_n), cfg,
                             f"Momentum top {cfg.momentum_top_n} ({cfg.momentum_lookback}d)", **kw),
        "sector_strength": simulate(px, lambda p: sector_strength_weights(p, sectors, cfg.sector_lookback, cfg.sector_top_k),
                                    cfg, f"Sector strength top {cfg.sector_top_k} ({cfg.sector_lookback}d)", **kw),
        "equal_weight_rebalanced": simulate(px, equal_weights, cfg, "Equal-weight, monthly rebalanced", **kw),
        "benchmark": bench,
    }
    start = max(r.returns.index[0] for r in results.values())
    for r in results.values():
        keep = r.returns.index >= start
        r.returns, r.gross_returns, r.turnover = r.returns[keep], r.gross_returns[keep], r.turnover[keep]
    return results


def summarize(results: dict) -> pd.DataFrame:
    b = results["benchmark"].returns
    rows = []
    for key, r in results.items():
        m = metrics(r.returns, None if key == "benchmark" else b, turnover=r.turnover)
        g = metrics(r.gross_returns)
        rows.append({"strategy": r.name, "key": key, **m, "cagr_before_costs": g["cagr"]})
    return pd.DataFrame(rows).set_index("key")


def apply_cost_to_picks(gross: pd.Series, picks: pd.Series, cost_bp: float) -> pd.Series:
    prev = picks.shift(1)
    turnover = np.where(prev.isna(), 1.0, np.where(picks != prev, 2.0, 0.0))
    return (1 + gross) * (1 - turnover * cost_bp / 1e4) - 1


def published_rotation(path: Path, cost_bp: float = REALISTIC_BP) -> dict:
    d = pd.read_csv(path)
    d.index = pd.PeriodIndex(d["month"], freq="M")
    rot = d["Rotation | 0bp"]
    check = apply_cost_to_picks(rot, d["pick"], 10.0)
    reproduction_error = float((check - d["Rotation | 10bp"]).abs().max())
    net = apply_cost_to_picks(rot, d["pick"], cost_bp)
    bench = d["Equal-weight buy-and-hold (25 stocks) | 0bp"]
    rebal = d["Equal-weight, monthly rebalanced | 0bp"]
    switches = int((d["pick"] != d["pick"].shift()).sum() - 1)
    series = {"rotation_gross": rot, "rotation_net": net, "benchmark": bench, "equal_weight_rebalanced": rebal}
    table = {k: metrics(v, None if k == "benchmark" else bench, periods_per_year=12) for k, v in series.items()}
    return {"months": len(d), "first_month": str(d.index[0]), "last_month": str(d.index[-1]),
            "switches": switches, "cost_bp": cost_bp, "reproduction_error_10bp": reproduction_error,
            "metrics": table, "series": series}


def load_quality_timeline(src) -> QualityTimeline:
    exists = src.query("SELECT to_regclass('public.quality_states') IS NOT NULL AS ok")["ok"].iloc[0]
    if not exists:
        return QualityTimeline()
    return QualityTimeline(src.query("""SELECT ticker, price_date, state, decided_at FROM quality_states
                                        ORDER BY decided_at, state_id"""))


def load_membership(src, universe: str = "dashboard_25") -> Membership | None:
    exists = src.query("SELECT to_regclass('public.universe_membership') IS NOT NULL AS ok")["ok"].iloc[0]
    if not exists:
        return None
    df = src.query("""SELECT ticker, valid_from, valid_to FROM v_universe_membership WHERE universe = %s""", (universe,))
    return Membership(df) if len(df) else None


def load_db_panel(url: str):
    from marketintel.datasource import DbSource
    src = DbSource(url)
    df = src.query("""SELECT price_date, ticker, sector_name, COALESCE(adj_close, close) AS px
                      FROM v_company_daily ORDER BY price_date, ticker""")
    return _panel(df)


def load_synthetic_panel(seed: int = 7, years: float = 10, sector_momentum: float = 0.0):
    from datetime import date, timedelta
    from marketintel.sample import SampleConfig, generate_prices
    end = date(2026, 10, 30)
    cfg = SampleConfig(start=end - timedelta(days=int(years * 365.25)), end=end, seed=seed,
                       sector_momentum=sector_momentum)
    df = generate_prices(cfg).rename(columns={"adj_close": "px"})
    return _panel(df)


def _panel(df: pd.DataFrame):
    df["price_date"] = pd.to_datetime(df["price_date"])
    px = df.pivot(index="price_date", columns="ticker", values="px").sort_index().astype(float)
    sectors = df.drop_duplicates("ticker").set_index("ticker")["sector_name"].to_dict()
    return px, sectors


COLORS = {"momentum": "#2a78d6", "sector_strength": "#eb6834", "equal_weight_rebalanced": "#1baf7a",
          "benchmark": "#52514e", "rotation_gross": "#eda100", "rotation_net": "#eb6834"}


def _style(ax, title, ylabel):
    ax.set_title(title, loc="left", fontsize=12, fontweight="bold", color="#0b0b0b")
    ax.set_ylabel(ylabel, color="#52514e")
    ax.grid(axis="y", color="#e7e6e2")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c3c2b7")
    ax.tick_params(colors="#52514e")
    ax.set_facecolor("#fcfcfb")


def plot_equity(curves: dict, labels: dict, title: str, path: Path, log: bool = True, note: str = ""):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (ax, dd) = plt.subplots(2, 1, figsize=(11, 6.6), sharex=True, gridspec_kw={"height_ratios": [3, 1.2]},
                                 facecolor="#fcfcfb")
    for key, r in curves.items():
        idx = r.index.to_timestamp() if isinstance(r.index, pd.PeriodIndex) else r.index
        eq = 100 * (1 + r).cumprod()
        dash = (0, (4, 2)) if key == "benchmark" else "-"
        ax.plot(idx, eq.values, label=labels[key], color=COLORS.get(key, "#6250d6"), lw=2, linestyle=dash)
        dd.plot(idx, 100 * (eq / eq.cummax() - 1).values, color=COLORS.get(key, "#6250d6"), lw=1.4, linestyle=dash)
    if log:
        ax.set_yscale("log")
        ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"₹{v:,.0f}"))
        ax.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
        lo, hi = ax.get_ylim()
        ax.set_yticks([v for v in (25, 50, 75, 100, 150, 200, 300, 400, 500, 700, 1000) if lo <= v <= hi])
    _style(ax, title, "Value of ₹100 (log scale)" if log else "Value of ₹100")
    _style(dd, "Drawdown", "%")
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    if note:
        fig.text(0.01, 0.005, note, fontsize=8, color="#52514e")
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_seed_study(df: pd.DataFrame, path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(11, 3.6), facecolor="#fcfcfb")
    keys = ["momentum", "sector_strength", "equal_weight_rebalanced"]
    names = {"momentum": "Momentum top 5", "sector_strength": "Sector strength top 1",
             "equal_weight_rebalanced": "Equal-weight rebalanced"}
    rng = np.random.default_rng(0)
    for i, k in enumerate(keys):
        v = 100 * df[df["strategy"] == k]["excess_cagr_vs_benchmark"].to_numpy()
        ax.scatter(i + rng.uniform(-0.08, 0.08, len(v)), v, s=30, color=COLORS[k], edgecolor="white", linewidth=1)
        ax.hlines(v.mean(), i - 0.25, i + 0.25, color="#0b0b0b", lw=2)
        ax.text(i + 0.28, v.mean(), f"mean {v.mean():+.1f} pp", va="center", fontsize=9, color="#52514e")
    ax.axhline(0, color="#c3c2b7", lw=1)
    ax.set_xticks(range(len(keys)), [names[k] for k in keys])
    ax.set_xlim(-0.5, len(keys) - 0.2)
    _style(ax, f"Synthetic random walks, {df['seed'].nunique()} seeds: excess CAGR vs buy-and-hold after costs",
           "pp per year")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def seed_study(seeds=range(1, 21), cfg: BacktestConfig = BacktestConfig()) -> pd.DataFrame:
    rows = []
    for world, mom in (("null", 0.0),):
        for s in seeds:
            px, sectors = load_synthetic_panel(seed=s, sector_momentum=mom)
            table = summarize(run_all(px, sectors, cfg))
            for key in ("momentum", "sector_strength", "equal_weight_rebalanced"):
                rows.append({"world": world, "seed": s, "strategy": key,
                             "excess_cagr_vs_benchmark": table.loc[key, "excess_cagr_vs_benchmark"],
                             "sharpe": table.loc[key, "sharpe"], "benchmark_sharpe": table.loc["benchmark", "sharpe"],
                             "hit_rate": table.loc[key, "hit_rate_vs_benchmark"]})
    return pd.DataFrame(rows)


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items() if k != "series"}
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def main():
    ap = argparse.ArgumentParser(description="Backtest the momentum and sector-strength signals")
    ap.add_argument("--source", choices=["db", "synthetic", "published"], default="synthetic")
    ap.add_argument("--out", type=Path, default=ROOT / "results" / "backtest")
    ap.add_argument("--img", type=Path, default=ROOT / "docs" / "img")
    ap.add_argument("--cost-bp", type=float, default=REALISTIC_BP)
    ap.add_argument("--lag", type=int, default=1)
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--rigour", action="store_true", help="cost levels, turnover, bootstrap, holdout split, trial registry")
    ap.add_argument("--holdout-years", type=float, default=3.0)
    ap.add_argument("--bootstrap-draws", type=int, default=2000)
    ap.add_argument("--block", type=int, default=21)
    ap.add_argument("--registry", choices=["auto", "db", "csv", "none"], default="auto")
    args = ap.parse_args()
    cfg = BacktestConfig(cost_bp=args.cost_bp, lag_days=args.lag)
    args.out.mkdir(parents=True, exist_ok=True)

    if args.source == "published":
        res = published_rotation(ROOT / "results" / "revision" / "task6_monthly_clean.csv", args.cost_bp)
        (args.out / "published_rotation.json").write_text(json.dumps(_jsonable(res), indent=2))
        labels = {"rotation_gross": "Sector strength rotation, no costs",
                  "rotation_net": f"Sector strength rotation, {args.cost_bp:g} bp one-way",
                  "equal_weight_rebalanced": "Equal-weight, monthly rebalanced",
                  "benchmark": "Equal-weight buy-and-hold (25 stocks)"}
        plot_equity(res["series"], labels, f"Real data: sector-strength rotation, {res['first_month']} to {res['last_month']}",
                    args.img / "backtest_real_sector_rotation.png",
                    note="Monthly returns measured on the production database (results/revision/task6_monthly_clean.csv). "
                         "Signal at month-end close, held the next month.")
        print(json.dumps(_jsonable(res), indent=2))
        return

    if args.source == "db":
        import os
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("DATABASE_URL is not set")
        px, sectors = load_db_panel(url)
        from marketintel.datasource import DbSource
        timeline = load_quality_timeline(DbSource(url))
        membership = load_membership(DbSource(url))
        tag, note = "database", (f"Daily adj_close from v_company_daily (read-only); {len(timeline)} quality states applied as "
                                 f"known at each decision; universe membership "
                                 f"{'as of each decision' if membership is not None else 'not available, today’s members used'}.")
    else:
        px, sectors = load_synthetic_panel()
        tag, note = "synthetic", "SYNTHETIC random-walk prices (marketintel/sample.py, seed 7): no real signal by construction."
        timeline = None
        membership = None

    results = run_all(px, sectors, cfg, timeline, membership)
    table = summarize(results)
    table.to_csv(args.out / f"summary_{tag}.csv")
    (args.out / f"summary_{tag}.json").write_text(json.dumps(
        {"config": asdict(cfg), "source": tag, "metrics": _jsonable(table.to_dict(orient="index"))}, indent=2))
    plot_equity({k: r.returns for k, r in results.items()}, {k: r.name for k, r in results.items()},
                f"{'Synthetic' if tag == 'synthetic' else 'Database'} backtest after {cfg.cost_bp:g} bp costs, {cfg.lag_days}-day execution lag",
                args.img / f"backtest_{tag}.png", note=note)
    print(table[["final_value_of_100", "cagr", "volatility", "sharpe", "max_drawdown", "hit_rate_vs_benchmark",
                 "turnover_per_year", "cagr_before_costs"]].round(4).to_string())
    if args.rigour:
        run_rigour(args, px, sectors, cfg, tag, timeline, membership)
    if tag == "synthetic" and args.seeds:
        study = seed_study(range(1, args.seeds + 1), cfg)
        study.to_csv(args.out / "synthetic_seed_study.csv", index=False)
        plot_seed_study(study, args.img / "backtest_synthetic_seeds.png")
        print(study.groupby(["world", "strategy"])[["excess_cagr_vs_benchmark", "hit_rate"]].agg(["mean", "std"]).round(4))


def run_rigour(args, px, sectors, cfg, tag, timeline, membership):
    import os
    import backtest_rigour as R
    conn = None
    if tag == "database" and args.registry in ("auto", "db"):
        import psycopg
        conn = psycopg.connect(os.environ["DATABASE_URL"])
    try:
        registry = R.registry_for(args.registry, args.out, conn)
        out = R.run(px, sectors, cfg, args.out, registry, tag, args.holdout_years, args.bootstrap_draws, args.block,
                    timeline=timeline, membership=membership)
    finally:
        if conn is not None:
            conn.close()
    print(out["markdown"])
    return out


if __name__ == "__main__":
    main()

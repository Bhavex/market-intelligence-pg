import csv
import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd

import strategy_backtest as B
from india_costs import REALISTIC_BP

HIGH_EXTRA_BP = 30.0
COST_LEVELS = {"zero": 0.0, "realistic": REALISTIC_BP, "high": round(REALISTIC_BP + HIGH_EXTRA_BP, 1)}
EULER_GAMMA = 0.5772156649015329
PERIODS = ("full", "development", "holdout")
SWEEP = {
    "momentum": [{"momentum_lookback": lb, "momentum_top_n": n} for lb, n in product((20, 60, 120), (3, 5, 8))],
    "sector_strength": [{"sector_lookback": lb, "sector_top_k": k} for lb, k in product((20, 60), (1, 2))],
}
N = NormalDist()


def strategy_fn(key: str, cfg: B.BacktestConfig, sectors: dict):
    if key == "momentum":
        return lambda p: B.momentum_weights(p, cfg.momentum_lookback, cfg.momentum_top_n)
    if key == "sector_strength":
        return lambda p: B.sector_strength_weights(p, sectors, cfg.sector_lookback, cfg.sector_top_k)
    return B.equal_weights


def params_of(key: str, cfg: B.BacktestConfig) -> dict:
    base = {"cost_bp": cfg.cost_bp, "lag_days": cfg.lag_days, "warmup_days": cfg.warmup_days}
    if key == "momentum":
        return {**base, "momentum_lookback": cfg.momentum_lookback, "momentum_top_n": cfg.momentum_top_n}
    if key == "sector_strength":
        return {**base, "sector_lookback": cfg.sector_lookback, "sector_top_k": cfg.sector_top_k}
    return {**base, "rebalance": "once" if key == "benchmark" else "monthly"}


def data_fingerprint(px: pd.DataFrame) -> str:
    h = hashlib.sha256()
    h.update(",".join(map(str, px.columns)).encode())
    h.update(np.ascontiguousarray(px.index.asi8).tobytes())
    h.update(np.ascontiguousarray(np.nan_to_num(px.to_numpy(dtype=float), nan=-1.0)).tobytes())
    return h.hexdigest()


def holdout_start(index: pd.DatetimeIndex, years: float) -> pd.Timestamp:
    cut = index[-1] - pd.DateOffset(days=int(round(years * 365.25)))
    return index[int(index.searchsorted(cut))]


def period_slice(r: pd.Series, period: str, cut: pd.Timestamp) -> pd.Series:
    if period == "development":
        return r[r.index < cut]
    if period == "holdout":
        return r[r.index >= cut]
    return r


def path_metrics(r: np.ndarray, periods_per_year: int = B.TRADING_DAYS) -> dict:
    r = np.atleast_2d(r)
    n = r.shape[1]
    growth = np.prod(1 + r, axis=1)
    cagr = growth ** (periods_per_year / n) - 1
    sd = r.std(axis=1, ddof=1)
    sharpe = np.where(sd > 0, r.mean(axis=1) / np.where(sd > 0, sd, 1) * math.sqrt(periods_per_year), np.nan)
    eq = np.cumprod(1 + r, axis=1)
    mdd = (eq / np.maximum.accumulate(eq, axis=1) - 1).min(axis=1)
    return {"cagr": cagr, "sharpe": sharpe, "max_drawdown": mdd}


def block_indices(n: int, block: int, draws: int, rng: np.random.Generator) -> np.ndarray:
    block = max(1, min(block, n))
    blocks = -(-n // block)
    starts = rng.integers(0, n, size=(draws, blocks))
    idx = (starts[:, :, None] + np.arange(block)[None, None, :]) % n
    return idx.reshape(draws, -1)[:, :n]


def bootstrap(returns: pd.DataFrame, benchmark: str | None = "benchmark", block: int = 21, draws: int = 2000,
              seed: int = 0, level: float = 0.95) -> pd.DataFrame:
    r = returns.dropna()
    idx = block_indices(len(r), block, draws, np.random.default_rng(seed))
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    rows = []
    sampled = {c: r[c].to_numpy()[idx] for c in r.columns}
    point = {c: path_metrics(r[c].to_numpy()) for c in r.columns}
    for c in r.columns:
        m = path_metrics(sampled[c])
        for name, values in m.items():
            rows.append({"strategy": c, "metric": name, "point": float(point[c][name][0]),
                         "lo": float(np.nanquantile(values, lo_q)), "hi": float(np.nanquantile(values, hi_q)),
                         "share_above_zero": float(np.nanmean(values > 0))})
        if benchmark and benchmark in r.columns and c != benchmark:
            mb = path_metrics(sampled[benchmark])
            for name in ("cagr", "sharpe"):
                diff = m[name] - mb[name]
                rows.append({"strategy": c, "metric": f"excess_{name}_vs_{benchmark}",
                             "point": float(point[c][name][0] - point[benchmark][name][0]),
                             "lo": float(np.nanquantile(diff, lo_q)), "hi": float(np.nanquantile(diff, hi_q)),
                             "share_above_zero": float(np.nanmean(diff > 0))})
    out = pd.DataFrame(rows)
    out.attrs.update({"block": block, "draws": draws, "seed": seed, "level": level, "observations": len(r)})
    return out


def turnover_report(results: dict, cfg: B.BacktestConfig) -> pd.DataFrame:
    rows = []
    for key, res in results.items():
        r, g, t = res.returns, res.gross_returns, res.turnover
        years = len(r) / B.TRADING_DAYS
        led = res.ledger
        costs = led.costs.reindex(r.index).fillna(0.0) if led is not None else pd.Series(0.0, index=r.index)
        nav = led.nav.reindex(r.index) if led is not None else (1 + r).cumprod()
        net, gross = B.metrics(r)["cagr"], B.metrics(g)["cagr"]
        trade_days = int((t > 0).sum())
        rows.append({"strategy": key, "cost_bp": cfg.cost_bp, "trade_days": trade_days,
                     "turnover_per_year": float(t.sum() / years) if years else float("nan"),
                     "mean_turnover_per_trade_day": float(t[t > 0].mean()) if trade_days else 0.0,
                     "costs_paid_pct_of_mean_nav": float(100 * costs.sum() / nav.mean()) if len(nav) else 0.0,
                     "cost_drag_cagr": float(gross - net), "cagr_before_costs": float(gross), "cagr": float(net)})
    return pd.DataFrame(rows).set_index("strategy")


def deflated_sharpe(returns: pd.Series, trial_sharpes_per_period, periods_per_year: int = B.TRADING_DAYS) -> dict:
    r = returns.dropna().to_numpy()
    t = len(r)
    sd = r.std(ddof=1)
    sr = r.mean() / sd if sd > 0 else float("nan")
    skew = float(((r - r.mean()) ** 3).mean() / r.std(ddof=0) ** 3) if sd > 0 else 0.0
    kurt = float(((r - r.mean()) ** 4).mean() / r.std(ddof=0) ** 4) if sd > 0 else 3.0
    trials = np.asarray([x for x in trial_sharpes_per_period if x is not None and np.isfinite(x)], dtype=float)
    n = max(len(trials), 1)
    var = float(trials.var(ddof=1)) if len(trials) > 1 else 0.0
    if n > 1 and var > 0:
        sr0 = math.sqrt(var) * ((1 - EULER_GAMMA) * N.inv_cdf(1 - 1 / n) + EULER_GAMMA * N.inv_cdf(1 - 1 / (n * math.e)))
    else:
        sr0 = 0.0
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr ** 2
    z = (sr - sr0) * math.sqrt(t - 1) / math.sqrt(denom) if t > 1 and denom > 0 and np.isfinite(sr) else float("nan")
    return {"sharpe_annual": float(sr * math.sqrt(periods_per_year)), "trials": int(n),
            "trial_sharpe_sd_annual": float(math.sqrt(var) * math.sqrt(periods_per_year)),
            "expected_max_sharpe_annual": float(sr0 * math.sqrt(periods_per_year)),
            "skew": skew, "kurtosis": kurt, "observations": t,
            "deflated_sharpe_probability": float(N.cdf(z)) if np.isfinite(z) else float("nan")}


@dataclass
class Trial:
    run_tag: str
    code_version: str
    source: str
    data_fingerprint: str
    data_start: str
    data_end: str
    strategy: str
    params: dict
    period: str
    period_start: str
    period_end: str
    cost_level: str
    cost_bp: float
    n_obs: int
    metrics: dict
    purpose: str
    selected: bool = False


def _clean(obj):
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    return obj


class CsvRegistry:
    kind = "csv"
    FIELDS = list(Trial.__dataclass_fields__) + ["registered_at", "trial_id"]

    def __init__(self, path: Path):
        self.path = Path(path)

    def _rows(self) -> list:
        if not self.path.exists():
            return []
        with self.path.open(newline="") as f:
            return list(csv.DictReader(f))

    def register(self, trial: Trial) -> int:
        rows = self._rows()
        tid = len(rows) + 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        with self.path.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=self.FIELDS)
            if new:
                w.writeheader()
            row = {**asdict(trial), "registered_at": datetime.now(timezone.utc).isoformat(), "trial_id": tid}
            row["params"] = json.dumps(_clean(row["params"]), sort_keys=True)
            row["metrics"] = json.dumps(_clean(row["metrics"]), sort_keys=True)
            w.writerow(row)
        return tid

    def trials(self) -> pd.DataFrame:
        rows = self._rows()
        df = pd.DataFrame(rows, columns=self.FIELDS)
        if len(df):
            df["params"] = df["params"].map(json.loads)
            df["metrics"] = df["metrics"].map(json.loads)
        return df


class DbRegistry:
    kind = "db"

    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def available(conn) -> bool:
        row = conn.execute("SELECT to_regclass('public.backtest_trials')::text").fetchone()
        return bool(row) and row[0] == "backtest_trials"

    def register(self, trial: Trial) -> int:
        d = asdict(trial)
        d["params"] = json.dumps(_clean(d["params"]), sort_keys=True)
        d["metrics"] = json.dumps(_clean(d["metrics"]), sort_keys=True)
        tid = self.conn.execute("""
            INSERT INTO backtest_trials (run_tag, code_version, source, data_fingerprint, data_start, data_end, strategy,
                                         params, period, period_start, period_end, cost_level, cost_bp, n_obs, metrics,
                                         purpose, selected)
            VALUES (%(run_tag)s, %(code_version)s, %(source)s, %(data_fingerprint)s, %(data_start)s, %(data_end)s,
                    %(strategy)s, %(params)s::jsonb, %(period)s, %(period_start)s, %(period_end)s, %(cost_level)s,
                    %(cost_bp)s, %(n_obs)s, %(metrics)s::jsonb, %(purpose)s, %(selected)s)
            RETURNING trial_id""", d).fetchone()[0]
        self.conn.commit()
        return tid

    def trials(self) -> pd.DataFrame:
        cur = self.conn.execute("""SELECT trial_id, registered_at, run_tag, code_version, source, data_fingerprint, strategy,
                                          params, period, cost_level, cost_bp, n_obs, metrics, purpose, selected
                                   FROM backtest_trials ORDER BY trial_id""")
        names = [c.name for c in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=names)


class Evaluator:
    def __init__(self, px, sectors, base: B.BacktestConfig, registry, source: str, holdout_years: float = 3.0,
                 timeline=None, membership=None, run_tag: str | None = None, code_version: str | None = None):
        self.px, self.sectors, self.base, self.registry, self.source = px, sectors, base, registry, source
        self.timeline, self.membership = timeline, membership
        self.cut = holdout_start(px.index, holdout_years)
        self.fingerprint = data_fingerprint(px)
        self.run_tag = run_tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.code_version = code_version or os.environ.get("GITHUB_SHA", "local")
        self.registered = []

    def simulate(self, key: str, cfg: B.BacktestConfig):
        rebalance = "once" if key == "benchmark" else "monthly"
        return B.simulate(self.px, strategy_fn(key, cfg, self.sectors), cfg, key, rebalance=rebalance,
                          timeline=self.timeline, membership=self.membership)

    def record(self, key, cfg, res, period, cost_level, purpose, benchmark=None, selected=False) -> dict:
        r = period_slice(res.returns, period, self.cut)
        if len(r) < 2:
            return {}
        b = None if benchmark is None else period_slice(benchmark.returns, period, self.cut)
        t = period_slice(res.turnover, period, self.cut)
        m = B.metrics(r, b, turnover=t)
        m["sharpe_per_period"] = float(r.mean() / r.std(ddof=1)) if r.std(ddof=1) > 0 else None
        trial = Trial(self.run_tag, self.code_version, self.source, self.fingerprint, str(self.px.index[0].date()),
                      str(self.px.index[-1].date()), key, params_of(key, cfg), period, str(r.index[0].date()),
                      str(r.index[-1].date()), cost_level, cfg.cost_bp, len(r), _clean(m), purpose, selected)
        tid = self.registry.register(trial) if self.registry is not None else None
        self.registered.append({"trial_id": tid, **asdict(trial)})
        return m

    def cost_sensitivity(self, levels: dict = COST_LEVELS) -> tuple[pd.DataFrame, dict]:
        rows, by_level = [], {}
        for level, bp in levels.items():
            cfg = replace(self.base, cost_bp=bp)
            res = {k: self.simulate(k, cfg) for k in ("momentum", "sector_strength", "equal_weight_rebalanced", "benchmark")}
            start = max(r.returns.index[0] for r in res.values())
            for r in res.values():
                keep = r.returns.index >= start
                r.returns, r.gross_returns, r.turnover = r.returns[keep], r.gross_returns[keep], r.turnover[keep]
            by_level[level] = res
            for key, r in res.items():
                for period in PERIODS:
                    m = self.record(key, cfg, r, period, level, "cost_sensitivity",
                                    None if key == "benchmark" else res["benchmark"])
                    if m:
                        rows.append({"cost_level": level, "cost_bp": bp, "strategy": key, "period": period, **m})
        return pd.DataFrame(rows), by_level

    def sweep(self, benchmark) -> tuple[pd.DataFrame, dict]:
        rows, chosen = [], {}
        for key, grid in SWEEP.items():
            best = None
            warmup = max(self.base.warmup_days, 1 + max(v for g in grid for k, v in g.items() if k.endswith("lookback")))
            for params in grid:
                cfg = replace(self.base, warmup_days=warmup, **params)
                res = self.simulate(key, cfg)
                m = self.record(key, cfg, res, "development", "realistic", "parameter_sweep", benchmark)
                rows.append({"strategy": key, **params, "period": "development", **m})
                if m and (best is None or _score(m) > _score(best[2])):
                    best = (cfg, res, m)
            cfg, res, _ = best
            hold = self.record(key, cfg, res, "holdout", "realistic", "selected_on_development", benchmark, selected=True)
            chosen[key] = {"params": {k: getattr(cfg, k) for k in SWEEP[key][0]}, "holdout": hold, "result": res}
            rows.append({"strategy": key, **chosen[key]["params"], "period": "holdout", "selected": True, **hold})
        return pd.DataFrame(rows), chosen

    def trial_sharpes(self, strategy: str, period: str = "development") -> list:
        df = self.registry.trials() if self.registry is not None else pd.DataFrame(self.registered)
        if df.empty:
            return []
        df = df[(df["strategy"] == strategy) & (df["period"] == period) & (df["source"] == self.source)]
        return [m.get("sharpe_per_period") for m in df["metrics"]]


def _score(m: dict) -> float:
    s = m.get("sharpe")
    return float(s) if s is not None and np.isfinite(s) else -np.inf


def registry_for(kind: str, out: Path, conn=None):
    if kind == "none":
        return None
    if kind in ("db", "auto") and conn is not None and DbRegistry.available(conn):
        return DbRegistry(conn)
    if kind == "db":
        raise SystemExit("backtest_trials does not exist yet; run src/setup_db.py or use --registry csv")
    return CsvRegistry(out / "trial_registry.csv")


def _fmt(x, pct=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{100 * x:.1f}%" if pct else f"{x:.2f}"


def markdown(ev: Evaluator, costs: pd.DataFrame, turnover: pd.DataFrame, boot: pd.DataFrame, sweep: pd.DataFrame,
             chosen: dict, dsr: dict, registry_kind: str, total_trials: int) -> str:
    lines = [f"### Backtest rigour ({ev.source}, {ev.px.index[0].date()} to {ev.px.index[-1].date()})", "",
             f"Holdout starts {ev.cut.date()} (final period, never used to pick parameters). "
             f"Trials registered this run: {len(ev.registered)}; in the {registry_kind} registry: {total_trials}.", "",
             "#### Cost sensitivity (full period)", "",
             "| strategy | zero | realistic | high |", "|---|---|---|---|"]
    full = costs[costs["period"] == "full"].pivot(index="strategy", columns="cost_level", values="cagr")
    for s in full.index:
        lines.append(f"| {s} | " + " | ".join(_fmt(full.loc[s].get(lv), True) for lv in COST_LEVELS) + " |")
    lines += ["", f"CAGR. Cost levels in bp one-way: {', '.join(f'{k} {v:g}' for k, v in COST_LEVELS.items())}.", "",
              "#### Turnover (realistic costs)", "", "| strategy | turnover / year | trade days | cost drag (CAGR) |",
              "|---|---|---|---|"]
    for s, r in turnover.iterrows():
        lines.append(f"| {s} | {r['turnover_per_year']:.2f} | {int(r['trade_days'])} | {_fmt(r['cost_drag_cagr'], True)} |")
    lines += ["", f"#### Block bootstrap, {boot.attrs['level']:.0%} intervals ({boot.attrs['draws']} draws, "
              f"{boot.attrs['block']}-session blocks)", "", "| strategy | metric | point | interval | P(>0) |",
              "|---|---|---|---|---|"]
    for _, r in boot.iterrows():
        pct = r["metric"] in ("cagr", "max_drawdown", "excess_cagr_vs_benchmark")
        above = "n/a" if r["metric"] == "max_drawdown" else f"{r['share_above_zero']:.2f}"
        lines.append(f"| {r['strategy']} | {r['metric']} | {_fmt(r['point'], pct)} | "
                     f"{_fmt(r['lo'], pct)} to {_fmt(r['hi'], pct)} | {above} |")
    lines += ["", "#### Chronological split", "", "| strategy | development CAGR | holdout CAGR | development Sharpe | holdout Sharpe |",
              "|---|---|---|---|---|"]
    real = costs[costs["cost_level"] == "realistic"].set_index(["strategy", "period"])
    for s in ("momentum", "sector_strength", "equal_weight_rebalanced", "benchmark"):
        dev, hold = real.loc[(s, "development")], real.loc[(s, "holdout")]
        lines.append(f"| {s} | {_fmt(dev['cagr'], True)} | {_fmt(hold['cagr'], True)} | {_fmt(dev['sharpe'])} | {_fmt(hold['sharpe'])} |")
    lines += ["", "#### Parameters picked on the development period only", "",
              "| strategy | picked | holdout CAGR | holdout Sharpe | trials | deflated Sharpe probability |", "|---|---|---|---|---|---|"]
    for s, c in chosen.items():
        d = dsr.get(s, {})
        lines.append(f"| {s} | {json.dumps(c['params'])} | {_fmt(c['holdout'].get('cagr'), True)} | "
                     f"{_fmt(c['holdout'].get('sharpe'))} | {d.get('trials', 'n/a')} | {_fmt(d.get('deflated_sharpe_probability'))} |")
    return "\n".join(lines) + "\n"


def run(px, sectors, base: B.BacktestConfig, out: Path, registry, source: str, holdout_years: float = 3.0,
        draws: int = 2000, block: int = 21, seed: int = 0, timeline=None, membership=None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    ev = Evaluator(px, sectors, base, registry, source, holdout_years, timeline, membership)
    costs, by_level = ev.cost_sensitivity()
    realistic = by_level["realistic"]
    turnover = pd.concat([turnover_report(by_level[lv], replace(base, cost_bp=bp)).assign(cost_level=lv)
                          for lv, bp in COST_LEVELS.items()])
    rets = pd.DataFrame({k: r.returns for k, r in realistic.items()}).dropna()
    boot = bootstrap(rets, "benchmark", block, draws, seed)
    sweep, chosen = ev.sweep(realistic["benchmark"])
    dsr = {}
    for key, c in chosen.items():
        dev = period_slice(c["result"].returns, "development", ev.cut)
        dsr[key] = deflated_sharpe(dev, ev.trial_sharpes(key))
    total = len(registry.trials()) if registry is not None else len(ev.registered)
    costs.to_csv(out / "cost_sensitivity.csv", index=False)
    turnover.to_csv(out / "turnover.csv")
    boot.to_csv(out / "bootstrap_ci.csv", index=False)
    sweep.to_csv(out / "parameter_sweep.csv", index=False)
    (out / "trials_this_run.json").write_text(json.dumps(_clean(ev.registered), indent=2, default=str))
    summary = {"source": source, "holdout_start": str(ev.cut.date()), "holdout_years": holdout_years,
               "bootstrap": boot.attrs, "cost_levels_bp": COST_LEVELS, "deflated_sharpe": dsr,
               "selected": {k: {"params": v["params"], "holdout": v["holdout"]} for k, v in chosen.items()},
               "trials_this_run": len(ev.registered), "trials_in_registry": total,
               "registry": registry.kind if registry is not None else "none", "data_fingerprint": ev.fingerprint}
    (out / "rigour_summary.json").write_text(json.dumps(_clean(summary), indent=2, default=str))
    md = markdown(ev, costs, turnover[turnover["cost_level"] == "realistic"], boot, sweep, chosen, dsr,
                  summary["registry"], total)
    (out / "rigour_summary.md").write_text(md)
    return {"summary": summary, "costs": costs, "turnover": turnover, "bootstrap": boot, "sweep": sweep,
            "markdown": md, "evaluator": ev}

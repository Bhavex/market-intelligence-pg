"""Assemble the Zenodo v2 pack from the artifact of a pinned `Data trust study` run.

    gh run download <run-id> -n trust-study-output -D study_artifact
    python src/build_zenodo_v2.py --artifact study_artifact --run-url <run url> --out results/revision/zenodo_v2

The artifact holds study_output/ (fragility_flip_rates.csv, backtest_sensitivity.csv, run_parameters.json, dataset/*)
and study_output.log. Only derived measurements are copied: no prices, no volumes. Nothing is uploaded anywhere.
"""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd

import india_costs

REPO = Path(__file__).resolve().parent.parent
COST_TABLE = REPO / "results" / "revision" / "task2_backtest_19oct_costs.csv"
SIGNALS = ["trend", "is_anomaly", "breakout_52w", "strong_breakout", "rsi_zone", "momentum_sign", "sector_leader"]
BACKTEST_SETTINGS = ["monthend_snapshot_100pct", "monthend_snapshot_25pct", "monthend_snapshot_5pct"]


def find(artifact: Path, name: str) -> Path:
    hits = sorted(artifact.rglob(name))
    if not hits:
        raise SystemExit(f"{name} not found under {artifact}")
    return hits[0]


def cost_schedule() -> pd.DataFrame:
    rows = [
        ("STT", "0.100% buy and sell", india_costs.STT_BP, india_costs.STT_BP, "NSE, SEBI Turnover Fees, STT and Other levies (rates w.e.f. 1 Apr 2026; page updated 17 Apr 2026), fetched 21 Oct 2026"),
        ("NSE transaction charge", "Rs 307 per crore each side", india_costs.NSE_TXN_BP, india_costs.NSE_TXN_BP, "NSE circular NSE/FA/73061, 27 Feb 2026, effective 1 Mar 2026"),
        ("SEBI turnover fee", "Rs 10 per crore each side", india_costs.SEBI_BP, india_costs.SEBI_BP, "NSE page above, fetched 21 Oct 2026"),
        ("Stamp duty", "0.015% buyer only", india_costs.STAMP_BUY_BP, 0.0, "NSE page above, fetched 21 Oct 2026"),
        ("Brokerage (delivery)", "Rs 0 (discount broker)", 0.0, 0.0, "Zerodha charges page, fetched 21 Oct 2026"),
        ("GST", "18% of (brokerage + SEBI fee + transaction charge)", india_costs.side_bp(True) - india_costs.STT_BP - india_costs.NSE_TXN_BP
         - india_costs.SEBI_BP - india_costs.STAMP_BUY_BP, india_costs.side_bp(False) - india_costs.STT_BP - india_costs.NSE_TXN_BP
         - india_costs.SEBI_BP, "NSE page (rate); base per Zerodha charges page, fetched 21 Oct 2026"),
    ]
    df = pd.DataFrame(rows, columns=["component", "rule", "buy_bp", "sell_bp", "source"])
    total = pd.DataFrame([("TOTAL", "", df.buy_bp.sum(), df.sell_bp.sum(), "one-way = average of buy and sell = "
                           f"{india_costs.one_way_bp():.2f} bp, used as {india_costs.REALISTIC_BP} bp")], columns=df.columns)
    return pd.concat([df, total], ignore_index=True).round(4)


def stability(bt: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for sc in BACKTEST_SETTINGS:
        d = bt[bt.scenario == sc].set_index("metric")["value"]
        rows.append({"setting": sc, "draws_per_replicate": int(d.get("draws_per_replicate")), "replicates": int(d.get("replicates")),
                     "median": d.get("final_value_median"), "p5": d.get("final_value_p5"), "p95": d.get("final_value_p95"),
                     "seed_spread_median": d.get("seed_spread_median"), "seed_spread_p5": d.get("seed_spread_p5"),
                     "seed_spread_p95": d.get("seed_spread_p95")})
    return pd.DataFrame(rows)


def md_table(df: pd.DataFrame) -> str:
    return "\n".join(["| " + " | ".join(df.columns) + " |", "|" + "---|" * len(df.columns)]
                     + ["| " + " | ".join(str(v) for v in r) + " |" for r in df.itertuples(index=False)])


def flip_table(flips: pd.DataFrame, universe="dashboard", scenario="intraday_snapshot") -> str:
    d = flips[(flips.universe == universe) & (flips.scenario == scenario)].set_index("signal")
    lines = ["| Signal | Flipped / compared | Flip rate | 95% cluster-robust | 95% block bootstrap |", "|---|---:|---:|---|---|"]
    for s in SIGNALS:
        if s not in d.index:
            continue
        r = d.loc[s]
        boot = "n/a" if pd.isna(r.boot_low) else f"{100 * r.boot_low:.2f}-{100 * r.boot_high:.2f}%"
        lines.append(f"| {s} | {int(r.n_flipped):,} / {int(r.n_compared):,} | {100 * r.flip_rate:.2f}% | "
                     f"{100 * r.ci_low:.2f}-{100 * r.ci_high:.2f}% | {boot} |")
    return "\n".join(lines)


README = """# Free end-of-day data quality for NSE stocks: dataset, version 2

Derived measurements only: basis-point differences, flags, timings, revision kinds, missing sessions and simulation
results. **No raw prices or volumes** (Yahoo Finance and the exchanges restrict redistribution); they can be rebuilt with
the code at https://github.com/Bhavex/market-intelligence-pg (MIT). Data licence: CC BY 4.0.

Version 2 is the dataset that matches the paper. It replaces version 1 (see ZENODO_DESCRIPTION.md for what changed).

## Provenance
| | |
|---|---|
| Workflow | `Data trust study`, {run_url} |
| Code commit | {commit} |
| Data cutoff | **{cutoff}** (every table is cut at this date; later loads and revisions cannot enter) |
| Fragility run | {frag_run_id}; seed {seed}; **{sims} repetitions** of each simulated error scenario |
| Month-end backtest | {bt_draws:,} simulated histories per setting, **{bt_reps} independent replicates** (seeds derived from {seed}) |
| Stock-days in the database at the cutoff | {n_stock_days:,} |
| Replica of the SQL signal engine vs the database | {validation} |

Row-level files keep rows with `price_date` up to the cutoff, but include every revision recorded for those rows up to the day the files were exported (21 Oct 2026). Revision counts can therefore be slightly higher than in a count taken earlier, for example when a dividend went ex after the study began. Point-in-time history starts on 10 Oct 2026 (23:19 UTC), so revision and settlement files cover only 10-19 Oct 2026.

## Files
{files}

## Month-end backtest: sensitivity and seed stability
Final value of Rs 100 in a monthly sector-rotation rule (illustration, not a strategy), when every / 25% / 5% of month-ends
are intraday snapshots. `seed_spread_*` is the largest minus the smallest of the {bt_reps} replicates.

{stability}

Baseline (no data errors): Rs {baseline} over {months} monthly decisions.

## Flip rates, intraday snapshot, dashboard stocks
{flips}

## Costs
`nse_delivery_cost_schedule.csv` is the NSE equity-delivery charge schedule (sources and dates inside) behind the
**{one_way} bp** one-way cost; `backtest_costs_19oct.csv` charges that cost, and others, to the same rule (19 Oct 2026 sample).

## Signal definitions
See DATASHEET.md, "Definitions" (RSI zone and momentum sign are defined there, including ties and thresholds).

## Checksums
`SHA256SUMS` lists the SHA-256 of every file.
"""

DESCRIPTION = """Version 2 of the open dataset behind "How Much Can You Trust Free Market Data? A Point-in-Time Study of Free End-of-Day Prices for NSE Stocks".

Derived measurements of how Yahoo Finance's free end-of-day prices for NSE stocks differ from the official NSE and BSE files, how they are revised after first publication, which sessions they miss, and how often seven trading signals change their answer under realistic data errors (simulation). No raw prices are included.

WHAT CHANGED FROM VERSION 1
- Data. v1 was exported on 20 Oct 2026; its simulation results came from an earlier study run (data through 16 Oct 2026, 20 repetitions per scenario), its row-level files ran to 19 Oct, and the paper quoted a still later run. v2 is pinned to data through {cutoff} and every file is cut at that date, so the dataset and the paper's numbers come from one state of the database.
- Simulation size. {sims} repetitions of each simulated error scenario (v1: 20). Flip rates and their intervals are therefore based on ten times as many simulated errors and will differ somewhat from v1.
- Backtest sensitivity. The month-end backtest now uses {bt_draws:,} simulated histories per setting and {bt_reps} independent replicates (v1: 100 histories); backtest_sensitivity.csv reports each replicate and the spread between them (seed_spread_*), and the spread between replicates of the median and the 5th/95th percentiles is reported against a tolerance of Rs {tol}: {stable}.
- New files. nse_delivery_cost_schedule.csv (NSE delivery charges, STT, exchange, SEBI, stamp duty, GST, with sources and dates; {one_way} bp one way) and backtest_costs_19oct.csv (the rotation rule and benchmarks net of cost, 19 Oct 2026 sample).
- Datasheet. Signal definitions now include the exact RSI-zone rule (above 70 = +1, below 30 = -1, else 0) and the momentum-sign rule (sign of 20-session momentum, computed unrounded), which v1 did not define.
- Unchanged: file names and columns of the row-level files (revisions, settlement, three agreement files, missing sessions, official file log, universe, quality by group), the definitions of agreement (within one tick or 1 bp), live capture and scale mismatch, and the licence (CC BY 4.0).

Cite the repository and the paper. Code (MIT): https://github.com/Bhavex/market-intelligence-pg
"""


def sha_lines(out: Path) -> str:
    return "\n".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in sorted(out.iterdir())
                     if p.is_file() and p.name != "SHA256SUMS") + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact", required=True, type=Path)
    ap.add_argument("--run-url", required=True)
    ap.add_argument("--commit", default="(see run)")
    ap.add_argument("--out", default=REPO / "results" / "revision" / "zenodo_v2", type=Path)
    ap.add_argument("--tolerance", type=float, default=2.0, help="Rs: largest allowed seed spread")
    a = ap.parse_args()

    ds = find(a.artifact, "dataset").parent if (a.artifact / "dataset").exists() else find(a.artifact, "DATASHEET.md").parent
    params = json.loads(find(a.artifact, "run_parameters.json").read_text())
    p = params["params"]
    bt = pd.read_csv(find(a.artifact, "backtest_sensitivity.csv"))
    flips = pd.read_csv(find(a.artifact, "fragility_flip_rates.csv"))

    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    for f in ds.iterdir():
        if f.is_file() and f.name != "SHA256SUMS":
            shutil.copy(f, out / f.name)
    stab = stability(bt)
    stab.to_csv(out / "backtest_seed_stability.csv", index=False)
    cost_schedule().to_csv(out / "nse_delivery_cost_schedule.csv", index=False)
    shutil.copy(COST_TABLE, out / "backtest_costs_19oct.csv")

    worst = stab[["seed_spread_median", "seed_spread_p5", "seed_spread_p95"]].max().max()
    stable = (f"largest spread over all three settings Rs {worst:.2f}" if worst <= a.tolerance
              else f"NOT MET: largest spread Rs {worst:.2f}")
    base = bt[(bt.scenario == "baseline")].set_index("metric")["value"]
    fmt = dict(
        run_url=a.run_url, commit=a.commit, cutoff=p.get("cutoff"), frag_run_id=params["frag_run_id"], seed=p["seed"],
        sims=p["sims"], bt_draws=int(p["bt_draws"]), bt_reps=int(p["bt_replicates"]),
        n_stock_days=int(params["n_stock_days"]),
        validation=", ".join(f"{k} {v}" for k, v in params["validation"].items() if k.endswith("_pct")),
        files="\n".join(f"* `{f.name}`" for f in sorted(out.iterdir()) if f.is_file()),
        stability=md_table(stab), baseline=base.get("final_value_rs"), months=int(base.get("months", 0)),
        flips=flip_table(flips), one_way=india_costs.REALISTIC_BP, tol=a.tolerance, stable=stable)
    (out / "README.md").write_text(README.format(**fmt))
    (out / "ZENODO_DESCRIPTION.md").write_text(DESCRIPTION.format(**fmt))
    (out / "SHA256SUMS").write_text(sha_lines(out))
    print(f"pack written to {out} ({len(list(out.iterdir()))} files); seed stability: {stable}")


if __name__ == "__main__":
    main()

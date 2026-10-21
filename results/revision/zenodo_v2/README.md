# Free end-of-day data quality for NSE stocks: dataset, version 2

Derived measurements only: basis-point differences, flags, timings, revision kinds, missing sessions and simulation
results. **No raw prices or volumes** (Yahoo Finance and the exchanges restrict redistribution); they can be rebuilt with
the code at https://github.com/Bhavex/market-intelligence-pg (MIT). Data licence: CC BY 4.0.

Version 2 is the dataset that matches the paper. It replaces version 1 (see ZENODO_DESCRIPTION.md for what changed).

## Provenance
| | |
|---|---|
| Workflow | `Data trust study`, https://github.com/Bhavex/market-intelligence-pg/actions/runs/39549402280 |
| Code commit | 560f9e4327aa6e1657b29a9b041aafa00b7952c7 |
| Data cutoff | **2026-10-19** (every table is cut at this date; later loads and revisions cannot enter) |
| Fragility run | 5; seed 42; **200 repetitions** of each simulated error scenario |
| Month-end backtest | 1,000,000 simulated histories per setting, **3 independent replicates** (seeds derived from 42) |
| Stock-days in the database at the cutoff | 211,859 |
| Replica of the SQL signal engine vs the database | trend_match_pct 100.0, anomaly_match_pct 100.0, breakout_match_pct 100.0, rsi_within_0.01_pct 100.0, mom20_within_1e-6_pct 100.0 |

Row-level files keep rows with `price_date` up to the cutoff, but include every revision recorded for those rows up to the day the files were exported (21 Oct 2026). Revision counts can therefore be slightly higher than in a count taken earlier, for example when a dividend went ex after the study began. Point-in-time history starts on 10 Oct 2026 (23:19 UTC), so revision and settlement files cover only 10-19 Oct 2026.

## Files
* `DATASHEET.md`
* `agreement_yahoo_bo_vs_bse.csv`
* `agreement_yahoo_ns_vs_bse.csv`
* `agreement_yahoo_ns_vs_nse.csv`
* `backtest_costs_19oct.csv`
* `backtest_seed_stability.csv`
* `backtest_sensitivity.csv`
* `fragility_results.csv`
* `fragility_run.json`
* `missing_sessions.csv`
* `nse_delivery_cost_schedule.csv`
* `official_file_log.csv`
* `quality_by_group.csv`
* `revisions.csv`
* `settlement.csv`
* `universe.csv`

## Month-end backtest: sensitivity and seed stability
Final value of Rs 100 in a monthly sector-rotation rule (illustration, not a strategy), when every / 25% / 5% of month-ends
are intraday snapshots. `seed_spread_*` is the largest minus the smallest of the 3 replicates.

| setting | draws_per_replicate | replicates | median | p5 | p95 | seed_spread_median | seed_spread_p5 | seed_spread_p95 |
|---|---|---|---|---|---|---|---|---|
| monthend_snapshot_100pct | 1000000 | 3 | 400.64 | 289.56 | 532.14 | 0.3 | 0.64 | 0.63 |
| monthend_snapshot_25pct | 1000000 | 3 | 456.84 | 355.58 | 519.34 | 0.0 | 0.07 | 0.02 |
| monthend_snapshot_5pct | 1000000 | 3 | 458.72 | 434.82 | 484.79 | 0.0 | 1.3 | 0.0 |

Baseline (no data errors): Rs 458.72 over 142 monthly decisions.

## Flip rates, intraday snapshot, dashboard stocks
| Signal | Flipped / compared | Flip rate | 95% cluster-robust | 95% block bootstrap |
|---|---:|---:|---|---|
| trend | 506,494 / 13,565,000 | 3.73% | 3.72-3.75% | 3.54-3.92% |
| is_anomaly | 500,075 / 13,565,000 | 3.69% | 3.67-3.70% | 3.45-3.92% |
| breakout_52w | 357,921 / 13,305,000 | 2.69% | 2.68-2.70% | 2.40-3.03% |
| strong_breakout | 94,635 / 13,305,000 | 0.71% | 0.71-0.72% | 0.61-0.84% |
| rsi_zone | 661,552 / 13,565,000 | 4.88% | 4.86-4.89% | 4.71-5.05% |
| momentum_sign | 475,700 / 13,565,000 | 3.51% | 3.50-3.52% | 3.34-3.68% |
| sector_leader | 36,046 / 542,600 | 6.64% | 6.58-6.71% | 5.74-7.45% |

## Costs
`nse_delivery_cost_schedule.csv` is the NSE equity-delivery charge schedule (sources and dates inside) behind the
**11.2 bp** one-way cost; `backtest_costs_19oct.csv` charges that cost, and others, to the same rule (19 Oct 2026 sample).

## Signal definitions
See DATASHEET.md, "Definitions" (RSI zone and momentum sign are defined there, including ties and thresholds).

## Checksums
`SHA256SUMS` lists the SHA-256 of every file.

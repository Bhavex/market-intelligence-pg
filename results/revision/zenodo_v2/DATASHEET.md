# Datasheet: free end-of-day data quality for NSE stocks

Version 2026-10-21. Produced by `src/export_dataset.py` of https://github.com/Bhavex/market-intelligence-pg (method: docs/TRUST.md).

## What it is
Measurements of how a free price feed (Yahoo Finance, NSE `.NS` and BSE `.BO` listings) differs from the exchanges' official end-of-day files (NSE and BSE bhavcopy), how often and how its values are revised after first publication, which trading sessions it misses, and how often common technical signals change their answer under realistic data errors (simulation results).

## What it is not
No raw price series are included. Yahoo Finance and the exchanges restrict redistribution of their data; all published values are differences (basis points / percent), flags, timestamps and counts. The prices can be rebuilt with the repository's code.

## Coverage
* Price history in the source database: 2015-01-01 to 2026-10-19 (all files cut at 2026-10-19).
* Version history (first prints and revisions) captured live since 2026-10-10T23:19:22.802034+00:00.
* Stocks: the 25-stock dashboard universe, every NIFTY 50 member, and 25 NIFTY Smallcap 100 members (every 4th by symbol) — see `universe.csv`.

## Files
| File | Rows | Columns |
|---|---:|---|
| `universe.csv` | 81 | ticker, company_name, sector_or_industry, cap_group, in_dashboard, index_membership, isin |
| `revisions.csv` | 6100 | ticker, price_date, revision_kind, old_known_from, revised_at, revised_hours_after_session, close_change_bp, adj_change_bp, old_source, new_source, volume_change_pct |
| `settlement.csv` | 6070 | ticker, price_date, n_versions, first_seen, last_changed, settled_hours_after_session, first_to_final_bp |
| `agreement_yahoo_ns_vs_nse.csv` | 19995 | ticker, price_date, diff_bp, agrees, matches_last_trade_instead, volume_diff_pct, first_print_diff_bp, first_print_agrees, live_captured, scale_mismatch, yahoo_source |
| `agreement_yahoo_ns_vs_bse.csv` | 19809 | ticker, price_date, diff_bp, agrees, scale_mismatch |
| `agreement_yahoo_bo_vs_bse.csv` | 11164 | ticker, price_date, diff_bp, agrees, first_print_agrees, live_captured, scale_mismatch, n_changes |
| `missing_sessions.csv` | 0 | ticker, price_date |
| `official_file_log.csv` | 504 | source, file_date, status, http_status, rows_matched, fetched_at |
| `quality_by_group.csv` | 3 | grp, n_stocks, n_stock_days, nse_compared, nse_agree_pct, nse_live_first_prints, nse_first_print_agree_pct, bse_feed_compared, bse_feed_agree_pct, bse_feed_live_first_prints, bse_feed_first_print_agree_pct, sessions_traded, sessions_missing_in_yahoo, missing_pct, value_revisions_per_1000, rescaled_rows |
| `fragility_results.csv` | 131 | frag_run_id, data_through, universe, scenario, signal, n_compared, n_flipped, flip_rate, ci_low, ci_high, boot_low, boot_high |
| `backtest_sensitivity.csv` | 74 | scenario, metric, value |

## Definitions
* `diff_bp`: (free close / official close − 1) × 10,000.
* `agrees`: within one tick (₹0.05) or 1 bp of the official close, whichever is larger.
* `live_captured`: the first version was recorded within 3 days of the session (a real first print, not backfill).
* `scale_mismatch`: ratio outside 0.8–1.25 — a split/bonus has rescaled the free history; not comparable.
* `revision_kind`: rule-based class of each change (docs/TRUST.md §2.2).
* Signals in `fragility_results.csv` (exact rules: docs/signals.md; the simulation is `src/fragility.py` `day_signals`): `trend` = UP if price > 50-day mean > 200-day mean, DOWN if price < 50-day mean < 200-day mean, else NEUTRAL (NEUTRAL with under 200 sessions of history); `is_anomaly` = one-day return beyond 3 standard deviations of the previous 20 returns, or volume z-score above 3; `breakout_52w` = price above the highest adjusted close of the previous 252 sessions; `strong_breakout` = breakout and volume z-score above 2; `rsi_zone` = +1 if the 14-day RSI (simple averages, not Wilder smoothing) is above 70, -1 if below 30, else 0 (exactly 30 or 70 is 0); `momentum_sign` = sign of price / price 20 sessions earlier - 1, computed unrounded (+1, -1, or 0 on an exact tie; days without 20 sessions of history are skipped); `sector_leader` = the sector (5 sectors, dashboard stocks) with the highest average 20-day momentum that day.
* Fragility `flip_rate`: share of comparisons whose signal answer changed; `ci_low`/`ci_high`: 95% cluster-robust interval (cluster = contaminated day × simulation); `boot_low`/`boot_high`: 95% moving-block bootstrap over trading days (20-day blocks), a robustness check.

## Known limitations
Short live-capture window; the intraday error model is a Brownian bridge inside the day's range; 25 small caps are a sample; one free provider. See docs/TRUST.md §4.

## Licence
The derived data in this folder: CC BY 4.0. Please cite the repository (CITATION.cff).

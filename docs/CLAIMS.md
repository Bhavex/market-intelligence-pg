# Claims audit

Every factual claim in `README.md`, `docs/RESUME.md` and `docs/DEMO_DEFENCE.md`, mapped to what proves it. Claims
that were stronger than their evidence have been **rewritten** in those files; the table shows the old wording, the
problem and the new wording. Audit date: 4 Nov 2026.

Status:
- **verified:** the evidence supports the claim as written.
- **qualified:** true, but only within a stated scope, which is now written next to the claim.
- **rewritten:** the old wording overstated the evidence and has been replaced.
- **measured locally:** a synthetic-data measurement on a local machine, not production.

## 1. The overstated claims

| Old wording | Where | Problem | New wording |
|---|---|---|---|
| "a wrong row is fixed by the next run" / the pipeline "stays correct on its own" | README *Pipeline behaviour*; DEMO_DEFENCE #26 | The heal window covers only the **last 5 sessions**. An older error persists until the weekly full re-download (`revision_scan.py`). And "fixed" only means "replaced by Yahoo's current value", which can itself be wrong (Yahoo is not authoritative). | "re-fetches the last 5 sessions every run, so an error *in that window* is replaced by Yahoo's latest value; older rows are re-checked weekly by `revision_scan.py`. Neither path can correct an error Yahoo keeps reporting." |
| "Bad rows … never crash the run" | README *Data quality*; DEMO_DEFENCE #24 | Validation catches *row-level* problems it knows about. A run still fails on network errors, database errors, an empty or unexpected response, or a bug. Rejections are best-effort: `record_rejections()` swallows its own errors. | "Rows that fail a validation rule are logged and skipped instead of aborting the load. Network or database failures still fail the ticker or the run (`tests/test_insert.py::test_one_failing_ticker_does_not_stop_the_others`, `::test_total_outage_turns_the_run_red`)." |
| "The backtest had no look-ahead bias" | RESUME bullet 5; README *Backtest* | The engine's **decision timing** avoids look-ahead (signals see data only up to the signal close; trades fill one session later). The **data** does not: (1) `adj_close` is today's revised series, rescaled after later dividends and splits; (2) the universe is today's 25 survivors; (3) until this branch, validation rejected a row using the *next* day's price (future-dependent cleaning). | "no look-ahead in decision timing (tested); the price history is today's revised snapshot and the universe is today's members, so the result is not point-in-time." See `docs/BACKTEST.md` and tasks 3, 5 and 7 of this branch. |
| "NOT VALID … so this file can never break the daily pipeline" / "the migration cannot break the run" | MIGRATION_NOTES; DEMO_DEFENCE #9 | `NOT VALID` only skips the scan of *old* rows. The run can still fail because: `ADD CONSTRAINT` needs an `ACCESS EXCLUSIVE` lock and waits behind long readers; only `check_violation` / `foreign_key_violation` are caught; a new or updated row that violates a constraint fails its statement (so a ticker fails); and the deferred FK fails the whole `setup_db.py` transaction at commit if a contradictory city is seeded. | "`NOT VALID` makes adding a constraint instant and keeps old violating rows from blocking it. It does not make the migration risk-free: lock waits, other error types and new violating rows can still fail a run." |
| "It matches an independent pandas implementation exactly" / "SQL and Python agreement proves the maths" | RESUME bullet 2 and source table; DEMO_DEFENCE #20 | Both implementations were written from the same specification by the same author. Agreement shows they are **consistent**, not that the specification is right. Shared misunderstandings agree perfectly; for example, both deliberately treat the first row's gain as 0 to mirror `GREATEST(NULL, 0)`. The comparison ran on 640 synthetic rows. | "The SQL engine agrees with an independent pandas re-implementation on every one of 640 synthetic test rows. Separately, hand-computed examples check the formulas (`tests/test_signals.py`). Agreement shows consistency, not correctness of the definitions." |
| "make impossible values unstorable" | README *Data quality* | CHECKs encode a short list of impossible values (non-positive prices, `high < low`, RSI outside 0–100, …). Wrong-but-plausible values are still storable. | "reject the specific impossible values the constraints encode" |
| "`guard_final_sessions()` refuses any earlier write, whatever the code path" | README *Pipeline behaviour* | Only writes made through `prices.upsert_rows()` pass the guard. Direct SQL bypasses it. | "refuses any earlier write made through `upsert_rows()`, the path every loader uses" |
| "Running the same load twice leaves the database identical" | DEMO_DEFENCE #19 | Price rows are unchanged, but `refresh_signals()` rewrites `signals.computed_at`, and each run adds `pipeline_runs` (and, on this branch, fetch-ledger) rows. | "leaves every price row unchanged; only run logs and `signals.computed_at` change" |
| "`prices_as_of(ts)` returns the table as it was known at that moment" | DEMO_DEFENCE #26 | Versioning started when `sql/08` was first applied (Oct 2026). Earlier history is a one-time seed reconstructed from the audit log, not an observation. | "for any moment since versioning began (Oct 2026); earlier rows are a one-time reconstruction" |
| "a delayed schedule or late Yahoo data never costs a session" | README *Architecture* | Three runs make a missed session much less likely, not impossible (for example, Yahoo missing a bar for longer than a day). | "reduces the chance that a delayed schedule or late data costs a session" |
| "CHECK and FK constraints … catch bad data and mistakes that application code would miss" | RESUME LinkedIn draft | Same over-reach as "unstorable". | "catch the impossible values they encode, even when application code misses them" |

## 2. What the row counts count

Three different numbers appear in the documents. They count different things:

| Number | What it counts | Source |
|---|---|---|
| **72,725** | real `daily_prices` rows for the **25 dashboard stocks**, 1 Jan 2015 – 13 Oct 2026 (about 2,909 sessions × 25) | `CHANGES.md`, measured on Neon on 13 Oct 2026 |
| **212,507** | real `daily_prices` rows for **81 stocks**: the 25 dashboard stocks plus the research universe (`sql/11_research.sql`): every NIFTY 50 member (56 large caps in all) and 25 small caps. Some research stocks have shorter histories, so this is about 2,620 rows per stock on average. | `docs/BENCHMARKS.md`, table sizes measured on Neon on 1 Nov 2026 |
| **259,600** | **synthetic** rows in the local performance study: 100 synthetic tickers (25 dashboard + 75 generated "SYN" tickers) × 2,596 generated weekdays (4 Nov 2016 – 4 Nov 2026). It is not production data; it was chosen to be about the size of the production table. | `results/performance/production_scale_100_tickers.json`, `scripts/load_sample_data.py --extra-tickers 75` |
| **64,900** | synthetic rows for 25 tickers × 2,596 weekdays (dashboard scale) | `results/performance/dashboard_scale_25_tickers.json` |

So "25 tickers over about 11.5 years" is about **72,700** real rows, as you would expect. The 259,600 figure is a
synthetic stand-in for the 81-stock production table (212,507 rows), not a count of the dashboard data. README,
RESUME and PERFORMANCE now say so wherever the figure appears.

## 3. Speed-up figures and their raw timings

Every speed-up quoted in the README or resume, with the file that holds **every individual timing**, not only the
median.

| Figure | Where quoted | Raw timings | Status |
|---|---|---|---|
| API page 0.09 ms vs 27.6 ms (≈300×; an earlier run measured 0.05 vs 21.5 ms, 414×) | README, RESUME | `results/performance/production_scale_100_tickers.json` → `results.indexed.api_prices_page.exec_ms_runs` and `results.no_indexes_at_all.api_prices_page.exec_ms_runs` | measured locally (synthetic, PostgreSQL 16) |
| Sector stats 0.19 ms materialized vs 233 ms plain view | README, RESUME | same file → `sector_daily_1y_matview`, `sector_daily_1y_view` | measured locally |
| Freshness 19.2 ms → 0.63 ms (first measured as 20.2 → 0.65) | README, RESUME | same file → `freshness_old_groupby` (the old definition, re-measured on this branch) and `freshness` (the current view) | measured locally |
| Latest price per stock 1.5 ms vs > 30 s | README | same file → `latest_prices_by_sector`; the no-index run records the timeout, not a time | measured locally |
| 131.5× / 169× / 573× | RESUME (as "131–573× on production"), DEMO_DEFENCE #13, #16 | **Only medians exist** for the 1 Nov 2026 Neon run (`docs/BENCHMARKS.md`): `src/benchmark.py` did not keep individual runs. It now writes them to `results/benchmarks/latest.json`, which the weekly `benchmark.yml` commits from its next run. | qualified: labelled "median of 7" until the next weekly run publishes the raw file |

The re-measured numbers on this branch replace the earlier medians in README and PERFORMANCE wherever they differ.

## 4. README claims

| Claim | Status | Evidence |
|---|---|---|
| 25 NSE large caps in 5 sectors | verified | `sql/02_seed.sql`; `tests/test_datasource.py::test_demo_has_25_companies_in_5_sectors` |
| Loads every weekday, three times a day | verified | `.github/workflows/daily_update.yml` (`40 11 * * 1-5`, `40 14 * * 1-5`, `40 1 * * 2-6`) |
| Validated before insert, idempotent upserts | verified | `src/validation.py`; `src/prices.py::UPSERT_SQL`; `tests/test_insert.py::test_counts_inserted_updated_unchanged`; `tests/test_pipeline.py::Database::test_unchanged_row_is_not_rewritten` |
| Signals computed by one PL/pgSQL function with window functions | verified | `sql/03_signals.sql::refresh_signals` |
| A failed **daily** run opens a GitHub issue | verified (daily workflow only) | `.github/workflows/daily_update.yml` `alert` job; `tests/test_pipeline_alert.py` |
| About 212,500 price rows | qualified (81 stocks, see §2) | `docs/BENCHMARKS.md` |
| BCNF schema; constraints added `NOT VALID` → `VALIDATE` | verified / qualified (see §1) | `docs/NORMALISATION.md`; `sql/12_integrity_and_reporting.sql`; `tests/test_db_integration.py::test_all_constraints_are_validated_on_a_clean_database` |
| Bitemporal price history | qualified (since Oct 2026) | `sql/08_point_in_time.sql`; `tests/test_trust.py::PointInTime::test_versions_and_time_travel` |
| Least-privilege read-only role | verified | `sql/05_audit_and_procedures.sql`; `tests/test_datasource.py::test_db_source_is_read_only`; `tests/test_powerbi_contract.py::test_readonly_role_can_still_read` |
| Index speed-ups | measured locally | §3 |
| Backtest on real Neon data: 9.6% / 10.2% vs 12.5% | verified | workflow run 41892962103; `results/backtest/summary_neon_2015-05_2026-11.csv` |
| 353 tests (was 267), most without a database | verified | `python -m pytest --collect-only -q`: 283 run without a database, 70 need one |
| Dashboard has 5 pages and a demo mode | verified | `app/streamlit_app.py`; `tests/test_dashboard.py` |
| API: one error envelope, write methods return 405, 34 tests | verified | `api/main.py`; `tests/test_api.py` |
| Docker: one command, read-only login cannot write | verified locally on 4 Nov 2026 | PR #7 description (`permission denied` on `DELETE`) |
| Hourly rows are insert-only | verified | `src/prices.py::INSERT_ONLY_SQL`; `tests/test_insert.py::test_hourly_fallback_is_insert_only` |
| No partial bars before 16:30 IST | qualified (via `upsert_rows`) | `tests/test_pipeline.py::GuardFinalSessions` |
| Every value change is versioned | qualified (normal DML; `TRUNCATE` bypasses row triggers) | `sql/08_point_in_time.sql` triggers |
| 10 post-load checks | verified | `src/post_load.py::CHECKS` |
| SQL engine checked against pandas in CI and daily | qualified (§1) | `tests/test_db_integration.py`; `src/verify_52w.py` |
| Tests never read `DATABASE_URL` / CI never touches production | verified | `tests.yml` sets its own service URL and has no secrets |

## 5. RESUME claims

| Claim | Status | Evidence |
|---|---|---|
| ~212,500 price rows "for 25 large caps" | **rewritten**: the rows are for 81 stocks (§2) | `docs/BENCHMARKS.md` |
| Three loads per trading day, 11 quality gates (was 10), failure issues | verified | as README |
| Signal engine "matches pandas exactly" | **rewritten** (§1) | `tests/test_db_integration.py` |
| ≈300×, 233 → 0.19 ms, 19.2 → 0.63 ms | measured locally; raw timings published (§3) | `results/performance/*.json` |
| 131–573× on production | qualified: medians of 7 runs, raw runs not kept for that date (§3) | `docs/BENCHMARKS.md` |
| 353 tests in CI on Python 3.12/3.13 + PostgreSQL 17 | verified | `.github/workflows/tests.yml` |
| Backtest bullet: "no look-ahead bias" | **rewritten** (§1) | `docs/BACKTEST.md` |

## 6. DEMO_DEFENCE claims

| # | Claim | Status | Evidence / change |
|---|---|---|---|
| 1–7 | ER model, BCNF with one controlled redundancy, natural keys, soft deletes, NUMERIC | verified | `docs/NORMALISATION.md`, `docs/SCHEMA.md`, `sql/01`, `sql/12` |
| 5 | Tata Motors handled by `replace_company()` | **rewritten** on this branch: instrument identity with dated membership (task 7) | `sql/18_instruments.sql` |
| 8 | Constraints on `daily_prices` | verified | `tests/test_db_integration.py::test_constraints_reject_impossible_prices` |
| 9 | "If an old row fails, the constraint stays NOT VALID" | qualified (§1) | `docs/MIGRATION_NOTES.md` |
| 10–11 | Deferred FK; why no `low ≤ close ≤ high` CHECK | verified | `tests/test_db_integration.py::test_city_region_must_match_its_state` |
| 12–17 | Indexes, partial index, materialized view, partitioning | verified (Neon medians) | `docs/BENCHMARKS.md`; raw timings from the next weekly run |
| 18 | Pipeline steps | verified | `daily_update.yml` |
| 19 | Idempotency "identical database" | **rewritten** (§1) | `tests/test_pipeline.py::Database::test_unchanged_row_is_not_rewritten` |
| 20 | "matches 100%" | **rewritten** (§1) | |
| 21–22 | 52-week window; 16:30 guard | verified / qualified | `tests/test_signals.py::TestBreakout52w`; `GuardFinalSessions` |
| 23 | One failing ticker does not block the others | verified | `tests/test_insert.py::TestBackfill::test_one_failing_ticker_does_not_stop_the_others` |
| 24–25 | Spike rejection uses the next session; "never crash" | **rewritten** (§1); the rule itself is replaced on this branch by timestamped quality states (task 3) | `src/quality.py` |
| 26 | Revisions overwritten and versioned; time travel | **rewritten** (§1) | `sql/08_point_in_time.sql` |
| 27 | Missed days are detected | verified | `src/post_load.py`, `v_data_freshness` |
| 28 | Secrets and roles | verified | `.env.example`, `.gitignore`, `sql/05` |
| 29 | Testing without the real database | verified | `tests/conftest.py`, `tests.yml` |
| 30 | Limitations | verified, extended on this branch | |

# Query performance study

How fast are the queries behind the dashboard and the API, and how much of that speed comes from the indexes?
Measured with `EXPLAIN (ANALYZE, BUFFERS)` on a **throwaway local PostgreSQL** loaded with synthetic data. The
production Neon database was never touched.

Script: `src/perf_study.py` · raw results: `results/performance/*.json` · loader: `scripts/load_sample_data.py`

## Set-up

| | Dashboard scale | Production scale |
|---|---|---|
| Tickers | 25 (the dashboard universe) | 100 (25 dashboard + 75 synthetic research stocks) |
| History | 10 years (Nov 2016 to Nov 2026) | 10 years |
| `daily_prices` rows | 64,900 | **259,600** (production has about 212,500, see `docs/BENCHMARKS.md`) |
| `signals` rows | 64,900 | 259,600 |
| Table size | | `daily_prices` 39 MB, `signals` 83 MB (production: 35 MB and 77 MB) |

Method:
- **Engine.** PostgreSQL 16.15 on Linux x86-64. Production runs 17 on Neon, so absolute times differ; the ratios are
  what carry over.
- **Statistics.** `VACUUM (ANALYZE)` first, so statistics and the visibility map are current; index-only scans need
  the latter.
- **Timing.** Each query runs once to warm the cache, then 9 times. The table reports the **median server execution
  time** (planning time excluded).
- **Settings.** `max_parallel_workers_per_gather = 0` and `jit = off`, so timings compare plans rather than worker
  start-up costs.
- **Three scenarios, all inside one transaction that is rolled back afterwards.** PostgreSQL's DDL is transactional,
  so `DROP INDEX` and `DROP CONSTRAINT` vanish on `ROLLBACK`:
  1. **Indexed:** the schema as deployed.
  2. **No secondary indexes:** drops `idx_daily_prices_date`, `idx_signals_date`, `idx_signals_anomaly`,
     `idx_signals_breakout` and `ux_mv_sector_daily`. The primary keys stay.
  3. **No indexes at all:** also drops the primary keys of `daily_prices` and `signals` (`CASCADE` removes the
     composite FK between them).
- **Timeout.** Each query has a 30-second `statement_timeout` inside a savepoint; a query that hits it is reported as
  "> 30,000 ms".

```bash
PERF_DATABASE_URL=postgresql://localhost/perf python src/perf_study.py --label production_scale_100_tickers
```

## Results at production scale (259,600 rows)

| Query | Used by | Indexed | No secondary indexes | No indexes at all | Index speed-up | Plan with indexes |
|---|---|---:|---:|---:|---:|---|
| `api_prices_page` | `GET /v1/companies/{ticker}/prices?order=desc`, page 1 | **0.05 ms** | 0.05 ms | 21.52 ms | **414×** | Index scan (`daily_prices_pkey`), backwards |
| `api_prices_count` | pagination total | **0.11 ms** | 0.07 ms | 18.54 ms | **164×** | Index-only scan (`daily_prices_pkey`) |
| `company_history_1y` | Company detail page, `/signals` | **1.62 ms** | 1.64 ms | 72.00 ms | **44×** | Index scan (`daily_prices_pkey`) + `signals_pkey` |
| `all_stocks_one_day` | one session across all stocks | **0.17 ms** | 8.33 ms | 19.16 ms | **116×** | Bitmap index scan (`idx_daily_prices_date`) |
| `latest_prices_by_sector` | Sector overview, `/v1/companies/{ticker}` | **1.60 ms** | 43.93 ms | **> 30,000 ms** | **> 18,000×** | Index-only scan (`idx_daily_prices_date`) for `MAX(price_date)`, PK lookups per stock |
| `anomalies_180d` | Anomalies page | **0.58 ms** | 38.63 ms | 40.49 ms | **70×** | Bitmap index scan (`idx_signals_anomaly`, partial) |
| `sector_strength_latest` | `/v1/sectors/strength` | **0.35 ms** | 0.38 ms | 69.22 ms | **197×** | Index-only scan (`daily_prices_pkey`) |
| `freshness` | Pipeline health, `/v1/pipeline/status` | **0.65 ms** | 0.66 ms | 586.51 ms | **898×** | Index-only scan (`daily_prices_pkey`), one probe per stock |
| `sector_daily_1y_view` | `v_sector_daily` (plain view) | 215.95 ms | 204.17 ms | 435.23 ms | 2.0× | Sequential scans + hash joins + aggregate |
| `sector_daily_1y_matview` | `mv_sector_daily` (materialized) | **0.21 ms** | 0.79 ms | 0.95 ms | 4.6× | Bitmap index scan (`ux_mv_sector_daily`) |

**Dashboard scale (64,900 rows)** shows the same pattern with smaller gaps: the API page is 0.06 ms against 6.17 ms
(96×), `latest_prices_by_sector` 0.93 ms against more than 30 s, freshness 0.63 ms against 138 ms, and the materialized
view 0.21 ms against 95 ms for the plain view. Full table: `results/performance/dashboard_scale_25_tickers.json`.

## What an examiner should take from this

**1. The composite primary key `(ticker, price_date)` does most of the work.** Every per-stock query (history,
pagination, count, latest row) is a range scan on it. The API's "newest page first" request is a *backward* index scan
that stops after 100 rows: 0.05 ms, against 21.5 ms when the whole table must be read and sorted. This is why the
schema has no separate `(ticker, price_date DESC)` index: B-trees can be read in either direction. Comparing scenarios
1 and 2 confirms it: dropping the secondary indexes leaves these queries unchanged.

**2. A secondary index earns its place only for a different access path.** `idx_daily_prices_date` serves queries that
start from a *date*, such as "all stocks on one session" (0.17 ms vs 8.33 ms) and the `MAX(price_date)` inside
`v_latest_prices_by_sector`. The PK cannot help there because `price_date` is its second column.

**3. Partial indexes are cheap and precise.** `idx_signals_anomaly … WHERE is_anomaly` indexes only the anomaly rows
(3.0% of `signals` in this sample), so the Anomalies page reads 230 buffers instead of 8,078 (0.58 ms vs 38.6 ms).

**4. Without indexes, a correlated query becomes catastrophic, not just slow.** `v_latest_prices_by_sector` uses
`LATERAL … ORDER BY price_date DESC LIMIT 1` for each stock and its previous close. With the PK each lookup is a single
index probe (1.6 ms in total). Without it, every lookup is a full scan of the table, repeated for each candidate row,
so the cost grows roughly quadratically. It did not finish in 30 seconds. The same query shape is excellent with the
right index and terrible without it.

**5. Materialization beats indexing for aggregates.** `v_sector_daily` aggregates the whole price and signal history
on every call. No index can avoid reading the rows it aggregates, so dropping indexes changes it only about 2×.
Storing the result (`mv_sector_daily`, refreshed `CONCURRENTLY` after each load) makes the same request about
**1,000× faster** (216 ms → 0.21 ms). The trade-off is staleness between refreshes and a refresh after every load.

**6. A real fix found by the study: `v_data_freshness`.** The first run measured this view at **20.21 ms** even with
every index present (`production_scale_100_tickers_before_freshness_fix.json`). The plan showed why: `MAX(price_date)
… GROUP BY ticker` read all 64,900 index entries of the 25 dashboard stocks to produce 25 maxima. The view now uses one
backward PK probe per stock (`LEFT JOIN LATERAL (… ORDER BY price_date DESC LIMIT 1)`) and computes the expected
trading day once in a CTE. That gives **0.65 ms, about 31× faster**, and the output is identical (`EXCEPT` in both
directions returns no rows). Column names and types are unchanged, so Power BI and the contract test are unaffected.
The flip side is visible in scenario 3: without the index the rewrite is *slower* than the old aggregate
(587 ms vs 67 ms). A `LATERAL … LIMIT 1` plan is a bet on the index, which is why the PK must never be dropped.

**7. The planner's choices are visible in BUFFERS.** For example, `company_history_1y` touches about 2,000 shared
buffers with the PK and about 19,900 without it. Shared buffers are 8 KB pages, so the time difference is mostly I/O
avoided, even when everything is already cached.

## Limitations

- **Synthetic data.** Value distributions, such as how many rows are anomalies, differ from real data, so row
  estimates and some plan choices could differ in production.
- **Single-user, warm-cache timings.** There is no concurrency, no cold-cache case and no network round trip. On
  Neon, latency to the server (tens of ms) dominates everything that runs in under 2 ms.
- **Version.** This is PostgreSQL 16 locally against 17 in production. The weekly `benchmark.yml` measures a few of
  these patterns on Neon itself (`docs/BENCHMARKS.md`).

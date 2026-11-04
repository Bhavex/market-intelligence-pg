# Query performance study

How fast are the queries behind the dashboard and the API, and how much of that speed comes from the indexes?
Measured with `EXPLAIN (ANALYZE, BUFFERS)` on a **throwaway local PostgreSQL** loaded with synthetic data. The
production Neon database was never touched.

Script: `src/perf_study.py` · raw results: `results/performance/*.json` · loader: `scripts/load_sample_data.py`

## Set-up

| | Dashboard scale | Production scale |
|---|---|---|
| Tickers | 25 (the dashboard universe) | 100 (25 dashboard + 75 generated `SYN…` tickers) |
| History | 2,596 generated weekdays, 4 Nov 2016 – 4 Nov 2026 | the same 2,596 weekdays |
| `daily_prices` rows | 25 × 2,596 = **64,900** | 100 × 2,596 = **259,600** |
| `signals` rows | 64,900 | 259,600 |
| Table size | | `daily_prices` 39 MB, `signals` 83 MB |

**What the row counts mean.** Both databases are **synthetic**. 259,600 is not the dashboard data: the real
25-stock dashboard table has about 72,700 rows (2015 onwards). The real production table holds 212,507 rows for 81
stocks. 259,600 was chosen to be about that size, so the "production scale" results describe a table like the real
one, not the real one. See `docs/CLAIMS.md` §2.

Method:
- **Engine.** PostgreSQL 16.15 on Linux x86-64. Production runs 17 on Neon, so absolute times differ; the ratios are
  what carry over.
- **Statistics.** `VACUUM (ANALYZE)` first, so statistics and the visibility map are current; index-only scans need
  the latter.
- **Timing.** Each query runs once to warm the cache, then 9 times. The tables report the **median server execution
  time** (planning time excluded) and, in brackets, the fastest and slowest of the 9 runs. **Every individual run** is
  in `results/performance/*.json` (`exec_ms_runs`, `plan_ms_runs`, `buffers_runs`).
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
- **Order effect.** The scenarios run in the order above, and the *indexed* one runs first, straight after `VACUUM`.
  Queries that use only the primary key are therefore a little *slower* in scenario 1 than in scenario 2 (for example
  0.09 ms vs 0.05 ms), although both use the same index. That gap is cache warm-up, not an effect of the secondary
  indexes; compare scenarios 1 and 3 for the effect of indexing.

```bash
PERF_DATABASE_URL=postgresql://localhost/perf python src/perf_study.py --label production_scale_100_tickers
```

## Results at production scale (259,600 synthetic rows)

Median of 9 runs, with min–max in brackets. The speed-up is scenario 3 ÷ scenario 1.

| Query | Used by | Indexed | No secondary indexes | No indexes at all | Speed-up | Plan with indexes |
|---|---|---:|---:|---:|---:|---|
| `api_prices_page` | `GET /v1/companies/{ticker}/prices?order=desc`, page 1 | **0.09 ms** (0.08–0.17) | 0.05 (0.05–0.12) | 27.55 (23.54–38.59) | **≈300×** | Index scan (`daily_prices_pkey`), backwards |
| `api_prices_count` | pagination total | **0.11** (0.10–0.12) | 0.06 (0.06–0.07) | 19.14 (18.57–24.10) | ≈170× | Index-only scan (`daily_prices_pkey`) |
| `company_history_1y` | Company detail page, `/signals` | **2.94** (1.71–3.24) | 1.63 (1.57–1.76) | 96.74 (82.64–116.20) | ≈33× | Index scan (`daily_prices_pkey`) + `signals_pkey` |
| `all_stocks_one_day` | one session across all stocks | **0.14** (0.13–0.20) | 8.46 (7.88–10.73) | 18.76 (18.18–19.92) | ≈130× | Bitmap index scan (`idx_daily_prices_date`) |
| `latest_prices_by_sector` | Sector overview, `/v1/companies/{ticker}` | **1.48** (0.82–1.67) | 44.26 (42.54–62.30) | **> 30,000** | > 20,000× | Index-only scan (`idx_daily_prices_date`) for `MAX(price_date)`, PK lookups per stock |
| `anomalies_180d` | Anomalies page | **0.36** (0.31–0.51) | 46.30 (45.57–48.27) | 43.81 (42.84–49.37) | ≈120× | Bitmap index scan (`idx_signals_anomaly`, partial) |
| `sector_strength_latest` | `/v1/sectors/strength` | **0.39** (0.35–0.44) | 0.42 (0.40–0.55) | 73.19 (72.28–97.42) | ≈190× | Index-only scan (`daily_prices_pkey`) |
| `freshness` | Pipeline health, `/v1/pipeline/status` | **0.63** (0.59–0.67) | 0.74 (0.63–1.47) | 549.13 (518.39–655.00) | ≈870× | Index-only scan (`daily_prices_pkey`), one probe per stock |
| `freshness_old_groupby` | the pre-rewrite `v_data_freshness` | 19.23 (18.84–31.33) | 22.07 (19.30–33.25) | 74.49 (67.99–95.03) | 4× | Index-only scan of every row of each stock |
| `sector_daily_1y_view` | `v_sector_daily` (plain view) | 233.28 (217.88–277.34) | 216.39 (208.22–306.57) | 431.83 (382.19–506.43) | 2× | Sequential scans + hash joins + aggregate |
| `sector_daily_1y_matview` | `mv_sector_daily` (materialized) | **0.19** (0.18–0.22) | 1.68 (0.98–1.88) | 1.05 (0.94–1.25) | 6× | Bitmap index scan (`ux_mv_sector_daily`) |

All values are in ms. An earlier run on the same data measured the API page at 0.05 ms against 21.5 ms (414×); the
ratio moves by tens of percent between runs, so it is quoted here as "about 300×".

**Dashboard scale (64,900 synthetic rows)** shows the same pattern with smaller gaps. Medians:
- API page: 0.10 ms against 6.17 ms;
- `latest_prices_by_sector`: 1.02 ms against more than 30 s;
- freshness: 0.64 ms against 31.2 ms for the old `GROUP BY` definition;
- sector stats: 0.20 ms materialized against 84.6 ms as a plain view.

Full table: `results/performance/dashboard_scale_25_tickers.json`.

## What an examiner should take from this

**1. The composite primary key `(ticker, price_date)` does most of the work.** Every per-stock query (history,
pagination, count, latest row) is a range scan on it. The API's "newest page first" request is a *backward* index scan
that stops after 100 rows: about 0.05–0.09 ms, against about 27 ms when the whole table must be read and sorted. This
is why the schema has no separate `(ticker, price_date DESC)` index: B-trees can be read in either direction.
Dropping only the secondary indexes (scenario 2) leaves these queries just as fast.

**2. A secondary index earns its place only for a different access path.** `idx_daily_prices_date` serves queries that
start from a *date*, such as "all stocks on one session" (0.14 ms vs 8.46 ms) and the `MAX(price_date)` inside
`v_latest_prices_by_sector`. The PK cannot help there because `price_date` is its second column.

**3. Partial indexes are cheap and precise.** `idx_signals_anomaly … WHERE is_anomaly` indexes only the anomaly rows
(3.0% of `signals` in this sample), so the Anomalies page reads about 230 buffers instead of about 8,100 (0.36 ms vs
46 ms).

**4. Without indexes, a correlated query becomes catastrophic, not just slow.** `v_latest_prices_by_sector` uses
`LATERAL … ORDER BY price_date DESC LIMIT 1` for each stock and its previous close. With the PK each lookup is a single
index probe (about 1.5 ms in total). Without it, every lookup is a full scan of the table, repeated for each candidate
row, so the cost grows roughly quadratically. It did not finish in 30 seconds. The same query shape is excellent with
the right index and terrible without it.

**5. Materialization beats indexing for aggregates.** `v_sector_daily` aggregates the whole price and signal history
on every call. No index can avoid reading the rows it aggregates, so dropping indexes changes it only about 2×.
Storing the result (`mv_sector_daily`, refreshed `CONCURRENTLY` after each load) makes the same request roughly
**1,000× faster** (233 ms → 0.19 ms). The trade-off is staleness between refreshes and a refresh after every load.

**6. A real fix found by the study: `v_data_freshness`.** The old definition (`MAX(price_date) … GROUP BY ticker`)
reads every index entry of the 25 dashboard stocks to produce 25 maxima. It is re-measured on this run as
`freshness_old_groupby`: **19.2 ms** (18.8–31.3). The view now uses one backward PK probe per stock
(`LEFT JOIN LATERAL (… ORDER BY price_date DESC LIMIT 1)`) and computes the expected trading day once in a CTE. That
gives **0.63 ms** (0.59–0.67), **about 30× faster**. On the 4 Nov test data the output was identical (`EXCEPT` in both
directions returned no rows). Column names and types are unchanged, so Power BI and the contract test are unaffected.
The flip side is visible in scenario 3: without the index the rewrite is *slower* than the old aggregate
(549 ms vs 74 ms). A `LATERAL … LIMIT 1` plan is a bet on the index.

**7. The planner's choices are visible in BUFFERS.** For example, `company_history_1y` touches about 2,004 shared buffers
with the PK and about 19,872 without it. Shared buffers are 8 KB pages, so most of the time difference is
work avoided, even when everything is already cached.

## Limitations

- **Synthetic data.** Value distributions, such as how many rows are anomalies, differ from real data, so row
  estimates and some plan choices could differ in production.
- **Single-user, warm-cache timings.** There is no concurrency, no cold-cache case and no network round trip. On
  Neon, latency to the server (tens of ms) dominates everything that runs in under 2 ms.
- **Version.** This is PostgreSQL 16 locally against 17 in production. The weekly `benchmark.yml` measures a few of
  these patterns on Neon itself (`docs/BENCHMARKS.md`).

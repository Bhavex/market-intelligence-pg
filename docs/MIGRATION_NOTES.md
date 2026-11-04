# Schema review and migration `sql/12_integrity_and_reporting.sql`

This file records the database review done on 4 Nov 2026 and explains every object the migration adds.
The migration is applied by `src/setup_db.py` like every other SQL file, so the daily workflow picks it up
with no change to any workflow file.

## How the migration stays safe on a live database

| Rule | Why |
|---|---|
| `CREATE … IF NOT EXISTS`, `CREATE OR REPLACE VIEW`, `ON CONFLICT DO NOTHING` | `setup_db.py` re-runs every file before every load, so each statement must be a no-op the second time |
| Constraints are added `NOT VALID`, then `VALIDATE CONSTRAINT` is tried in its own exception block | `NOT VALID` is instant (no scan) and never fails on old rows. Validation runs once; if an old row breaks the rule, the constraint stays `NOT VALID` with a `NOTICE`, and new and updated rows are **still checked**. It validates itself on a later run once the data is clean (tested locally) |
| Only new views; no existing view is changed | `v_sector_daily`, `v_company_daily` and `mv_sector_daily` are what Power BI and the site read today. Changing their columns would break them, and dropping them would lose grants |
| `GRANT SELECT … TO readonly_access` | Power BI connects through the read-only role |

I checked the migration on a local PostgreSQL 16 with synthetic data. I applied it twice, broke a row on purpose
and re-applied it, then cleaned the row and re-applied it again. The CI integration job applies it twice to an empty
PostgreSQL 17 on every push.

## Normalisation review

| Table | Key | Normal form | Notes |
|---|---|---|---|
| `sectors` | `sector_id` (surrogate), `sector_name` UNIQUE | BCNF | |
| `cities` | `city_id`, UNIQUE `(city_name, state)` | **2NF only, before this migration** | `state → region` is a transitive dependency (`city_id → state → region`), and `state` is not a key |
| `states` *(new)* | `state` | BCNF | holds the `state → region` fact once |
| `companies` | `ticker` | BCNF | `sector_id`, `hq_city_id` are FKs |
| `daily_prices` | `(ticker, price_date)` | BCNF | weak entity of `companies` |
| `signals` | `(ticker, signal_date)` = FK to `daily_prices` | BCNF | derived data, kept apart from raw data on purpose (recomputable) |
| `fundamentals` | `(ticker, snapshot_date)` | BCNF | |
| `price_versions` | `version_id`; one current row per `(ticker, price_date)` (partial unique index) | BCNF | bitemporal history |
| `pipeline_runs`, `audit_log`, `rejected_prices` | surrogate keys | BCNF | logs |

**Fixing `cities` without breaking anything.** The textbook fix is to drop `cities.region` and look it up through
`states`. That would break `v_company_daily`, `v_region_daily` and every Power BI visual that uses `region`. The
migration takes a middle path instead:

1. `states(state PK, region, UNIQUE(state, region))` stores the dependency once.
2. A **composite foreign key** `cities(state, region) → states(state, region)` means a city's region must be its
   state's region. The redundant column stays, but it can no longer disagree with `states`, so the
   update anomaly is gone. `ON UPDATE CASCADE` moves a corrected region into every city of that state.
3. The FK is `DEFERRABLE INITIALLY DEFERRED`. `setup_db.py` runs all files in one transaction and `02_seed.sql` (cities)
   runs before `12_…` (states), so the check has to wait until commit.

This is controlled redundancy: the column is kept for read convenience and the FK keeps it consistent. It is
the same trade-off as `mv_sector_daily`.

## Constraints added

| Constraint | Rule | Why |
|---|---|---|
| `daily_prices_open_pos_chk`, `_high_pos_chk`, `_low_pos_chk` | `open/high/low IS NULL OR > 0` | Only `close > 0` existed. A zero open (a known Yahoo artefact) was caught only after loading, by `post_load.py` |
| `daily_prices_adj_pos_chk` | `adj_close IS NULL OR > 0` | `adj_close` feeds every signal; a zero gives division by zero or `-100%` returns |
| `daily_prices_date_range_chk` | `price_date >= 1990-01-01` | catches epoch/parsing bugs (`1970-01-01`). Future dates are already blocked by the `validate_price` trigger |
| `signals_rsi_range_chk` | `rsi_14 BETWEEN 0 AND 100` | the mathematical range of RSI; anything else is a signal-engine bug |
| `signals_volatility_nonneg_chk` | `volatility_20d >= 0` | a standard deviation cannot be negative |
| `signals_anomaly_reason_chk` | `is_anomaly = (anomaly_reason IS NOT NULL)` | the two columns must agree, otherwise `v_anomalies` and anomaly counts disagree |
| `fundamentals_nonneg_chk` | `market_cap, dividend_yield >= 0` | negative values are impossible. Revenue, EPS and P/E can legitimately be negative, so they are not checked |
| `pipeline_runs_times_chk`, `_rows_nonneg_chk` | `finished_at >= started_at`, `rows_upserted >= 0` | keep the monitoring table trustworthy |
| `companies_ticker_upper_chk` | `ticker = upper(ticker)` | the price trigger upper-cases tickers; a lower-case company ticker could never match its prices |
| `price_versions_ticker_fk` | `ticker → companies(ticker) ON UPDATE CASCADE` | the version table had no FK, so orphan history was possible |
| `cities_state_region_fk` | see above | 3NF |

**Deliberately not added:**
- `CHECK (low <= close <= high)`. Yahoo's NSE data has real bars where the close is a few paise outside the range
  (closing auction vs continuous trading). A hard rule would reject real data. It stays a warning, both in
  `src/validation.py` and in `post_load.py`.
- A FK from `price_versions.run_id` / `alt_feed_prices.run_id` to `pipeline_runs`. Provenance ids come in through
  `set_config('app.run_id')`. History must survive if old `pipeline_runs` rows are pruned, and the existing tests use
  synthetic run ids. `rejected_prices.run_id` *does* have the FK (`ON DELETE SET NULL`), because it is only written by
  the pipeline.

## New table: `rejected_prices`

This table logs rows that `src/validation.py` refused to insert: the check name, a readable detail, the raw row as
JSONB and the pipeline run. A rejected row then still leaves evidence you can query instead of disappearing.
`post_load.py` warns when rows were rejected in the last 7 days, and `v_rejections_summary` rolls them up per day and check.

## Indexes

| Index | Serves | Why it pays off |
|---|---|---|
| `idx_signals_breakout` `ON signals(signal_date) WHERE breakout_52w` | "which stocks broke out on day X / this month" | partial index: breakout days are a small fraction of all rows, so the index is tiny and the planner skips the rest of the table. Same pattern as the existing `idx_signals_anomaly` |
| `idx_audit_log_row_key` `(table_name, row_key)` | "show every correction of `INFY.NS|2026-10-13`" | audit lookups by key were sequential scans of a growing JSONB table |
| `idx_pipeline_runs_problems` `(started_at DESC) WHERE status <> 'success'` | health page: latest failed/partial runs | partial: successful runs (the vast majority) are not indexed |
| `idx_fundamentals_snapshot` `(snapshot_date)` | "latest snapshot for every stock" | the PK starts with `ticker`, so it cannot serve a date-first filter |

**Indexes considered and rejected:**
- `daily_prices(ticker, price_date DESC)`: the primary key B-tree is scanned backwards for "latest row per ticker".
  A second index would only slow down inserts.
- Indexes on `companies(is_active)` or `in_dashboard`: the table has about 80 rows, so a sequential scan is always
  cheaper.
- BRIN on `daily_prices(price_date)`: it would be fine for a table of tens of millions of rows. At about 210k rows (dashboard plus research
  stocks, see `docs/BENCHMARKS.md`) the existing B-tree is fast and already in use.

## Views for Power BI

| View | Grain | Use |
|---|---|---|
| `v_latest_prices_by_sector` | 1 row per **active** dashboard stock | KPI cards and tables: latest close, previous close, day change %, trend, RSI, 52-week distance, rank within sector, `is_current` (FALSE if the stock is stale). Uses `LATERAL … ORDER BY price_date DESC LIMIT 1`, so each stock is one backward primary-key lookup |
| `v_sector_daily_performance` | 1 row per sector per trading day | line charts: equal-weighted return, median, advancers/decliners, best/worst stock, volume, turnover (₹), and a **sector index rebased to 100** (compounded `EXP(SUM(LN(1+r)))` window) |
| `v_sector_returns_summary` | 1 row per sector, latest day | sector scorecard: 1D, 1W (5 sessions), 1M (21), 3M (63), YTD %, rank by 1M |
| `v_rejections_summary` | 1 row per day per validation check | data-quality page |

Design choices:
- **Equal-weighted** like the existing `v_sector_daily`, so the numbers agree with what the team already reports.
  There are no share counts in the schema, so a market-cap weighting would need `fundamentals.market_cap`, which is
  weekly and incomplete.
- **Returns use `adj_close`**, so dividends and splits do not show up as fake drops.
- `v_sector_daily_performance` keeps **inactive** stocks in history (no survivorship bias). `v_latest_prices_by_sector`
  shows **active** stocks only.
- These are plain views. Power BI in Import mode reads them once per refresh. If DirectQuery becomes slow, materialise
  `v_sector_daily_performance` like `mv_sector_daily` and refresh it in `post_load.py`.

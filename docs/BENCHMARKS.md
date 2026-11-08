# Query performance benchmarks

_Generated automatically by `src/benchmark.py` on 08 Nov 2026 (PostgreSQL 17.11 on Neon, free tier). Median of 7 runs after a warm-up; every run is wrapped in a rolled-back transaction._

| Test | Before | After | Speed-up |
|---|---|---|---|
| B-tree index: one stock, last 12 months | Forced sequential scan: 15.50 ms | Primary-key index: 0.14 ms | **114.8×** |
| Partial index: anomalies, last 90 days | Forced sequential scan: 40.62 ms | idx_signals_anomaly (partial): 0.34 ms | **118.4×** |
| Materialized view: sector stats, last 12 months | Plain view (aggregates every time): 128.26 ms | Materialized view (precomputed): 0.25 ms | **517.2×** |
| Partitioning: one year, average close per stock | Normal table (scans all years): 18.90 ms | Year-partitioned table (pruned to 1 of 13 partitions): 4.92 ms | **3.8×** |

## Query plans used

**B-tree index: one stock, last 12 months**
- Forced sequential scan: Seq Scan (daily_prices)
- Primary-key index: Bitmap Heap Scan (daily_prices), Bitmap Index Scan (daily_prices_pkey)

**Partial index: anomalies, last 90 days**
- Forced sequential scan: Seq Scan (signals)
- idx_signals_anomaly (partial): Bitmap Heap Scan (signals), Bitmap Index Scan (idx_signals_anomaly)

**Materialized view: sector stats, last 12 months**
- Plain view (aggregates every time): 7 scans: Bitmap Heap Scan (daily_prices), Bitmap Index Scan (idx_daily_prices_date), …
- Materialized view (precomputed): Bitmap Heap Scan (mv_sector_daily), Bitmap Index Scan (ux_mv_sector_daily)

**Partitioning: one year, average close per stock**
- Normal table (scans all years): Seq Scan (daily_prices)
- Year-partitioned table (pruned to 1 of 13 partitions): Seq Scan (daily_prices_part_y2025)

## Signal engine

`refresh_signals()` recomputes 30 days of signals for all stocks in **4805 ms** (window functions over ~400 days of look-back per stock).

## Table sizes

| Table | Rows (approx.) | Size on disk |
|---|---|---|
| `price_versions` | 231,804 | 93 MB |
| `signals` | 212,912 | 77 MB |
| `daily_prices` | 212,923 | 35 MB |
| `quality_states` | 85,561 | 21 MB |
| `price_observations` | 46,010 | 18 MB |
| `reference_prices` | 41,884 | 10000 kB |
| `audit_log` | 7,562 | 5768 kB |
| `mv_sector_daily` | 14,630 | 2576 kB |
| `alt_feed_prices` | 13,171 | 2344 kB |
| `fetch_log` | 1,605 | 1232 kB |
| `price_selection` | 3,275 | 1032 kB |
| `reconciliation_results` | 405 | 232 kB |
| `fragility_results` | 827 | 200 kB |
| `pipeline_runs` | 278 | 168 kB |
| `revision_captures` | 345 | 160 kB |
| `reference_fetch_log` | 530 | 160 kB |
| `fundamentals` | 349 | 120 kB |
| `companies` | 82 | 104 kB |
| `revision_capture_coverage` | 324 | 96 kB |
| `backtest_sensitivity` | 170 | 80 kB |
| `sectors` | 22 | 80 kB |
| `instrument_symbols` | 163 | 80 kB |
| `universe_membership` | 82 | 72 kB |
| `run_leases` | 1 | 56 kB |
| `run_lease_log` | 65 | 48 kB |
| `fetch_settings` | 2 | 48 kB |
| `instruments` | 82 | 40 kB |
| `cities` | 9 | 40 kB |
| `states` | 7 | 40 kB |
| `schema_migrations` | 22 | 32 kB |
| `market_holidays` | 16 | 32 kB |
| `fragility_runs` | 7 | 32 kB |
| `rejected_prices` | 0 | 32 kB |
| `calendar_coverage` | 1 | 32 kB |
| `trading_suspensions` | 0 | 24 kB |
| `backtest_trials` | 0 | 24 kB |

## How the partitioned table is built

```sql
DROP TABLE IF EXISTS daily_prices_part CASCADE;
CREATE TABLE daily_prices_part
                (LIKE daily_prices INCLUDING DEFAULTS INCLUDING CONSTRAINTS)
                PARTITION BY RANGE (price_date);
CREATE TABLE daily_prices_part_y2015 PARTITION OF daily_prices_part FOR VALUES FROM ('2015-01-01') TO ('2016-01-01');
CREATE TABLE daily_prices_part_y2016 PARTITION OF daily_prices_part FOR VALUES FROM ('2016-01-01') TO ('2017-01-01');
-- ... one partition per year ...
ALTER TABLE daily_prices_part ADD PRIMARY KEY (ticker, price_date);
INSERT INTO daily_prices_part SELECT * FROM daily_prices;
ANALYZE daily_prices_part;
```

## Notes

- At ~73k rows the whole database fits in memory, so absolute times are small; the speed-ups show how each technique scales as data grows (e.g. to all ~2,000 NSE stocks).
- Partitioning lets PostgreSQL skip every year except the one queried (*partition pruning*), without needing an index — so indexes are disabled for both sides of that test. The production table is not partitioned yet: at this size the date index gives similar speed; `daily_prices_part` is a benchmark copy showing how the table would scale.
- The materialized view is refreshed with `REFRESH MATERIALIZED VIEW CONCURRENTLY` after each daily load, so dashboards never wait for aggregation and readers are never blocked.

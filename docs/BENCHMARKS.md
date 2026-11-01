# Query performance benchmarks

_Generated automatically by `src/benchmark.py` on 01 Nov 2026 (PostgreSQL 17.11 on Neon, free tier). Median of 7 runs after a warm-up; every run is wrapped in a rolled-back transaction._

| Test | Before | After | Speed-up |
|---|---|---|---|
| B-tree index: one stock, last 12 months | Forced sequential scan: 15.91 ms | Primary-key index: 0.12 ms | **131.5×** |
| Partial index: anomalies, last 90 days | Forced sequential scan: 41.23 ms | idx_signals_anomaly (partial): 0.24 ms | **169.0×** |
| Materialized view: sector stats, last 12 months | Plain view (aggregates every time): 132.41 ms | Materialized view (precomputed): 0.23 ms | **573.2×** |
| Partitioning: one year, average close per stock | Normal table (scans all years): 19.38 ms | Year-partitioned table (pruned to 1 of 13 partitions): 4.79 ms | **4.0×** |

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

`refresh_signals()` recomputes 30 days of signals for all stocks in **4714 ms** (window functions over ~400 days of look-back per stock).

## Table sizes

| Table | Rows (approx.) | Size on disk |
|---|---|---|
| `price_versions` | 228,113 | 91 MB |
| `signals` | 212,507 | 77 MB |
| `daily_prices` | 212,507 | 35 MB |
| `reference_prices` | 41,084 | 9840 kB |
| `audit_log` | 7,061 | 4936 kB |
| `mv_sector_daily` | 14,605 | 2576 kB |
| `alt_feed_prices` | 12,816 | 2192 kB |
| `fragility_results` | 827 | 200 kB |
| `reference_fetch_log` | 520 | 152 kB |
| `pipeline_runs` | 208 | 104 kB |
| `companies` | 81 | 96 kB |
| `fundamentals` | 268 | 88 kB |
| `backtest_sensitivity` | 170 | 80 kB |
| `sectors` | 22 | 80 kB |
| `cities` | 9 | 40 kB |
| `fragility_runs` | 7 | 32 kB |
| `market_holidays` | 16 | 32 kB |

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

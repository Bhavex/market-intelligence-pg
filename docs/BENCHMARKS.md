# Query performance benchmarks

_Generated automatically by `src/benchmark.py` on 11 Oct 2026 (PostgreSQL 17.11 on Neon, free tier). Median of 7 runs after a warm-up; every run is wrapped in a rolled-back transaction._

| Test | Before | After | Speed-up |
|---|---|---|---|
| B-tree index: one stock, last 12 months | Forced sequential scan: 4.03 ms | Primary-key index: 0.12 ms | **33.3×** |
| Partial index: anomalies, last 90 days | Forced sequential scan: 6.51 ms | idx_signals_anomaly (partial): 0.14 ms | **44.9×** |
| Materialized view: sector stats, last 12 months | Plain view (aggregates every time): 34.45 ms | Materialized view (precomputed): 0.25 ms | **136.2×** |
| Partitioning: one year, average close per stock | Normal table (scans all years): 5.21 ms | Year-partitioned table (pruned to 1 of 13 partitions): 2.09 ms | **2.5×** |

## Query plans used

**B-tree index: one stock, last 12 months**
- Forced sequential scan: Seq Scan (daily_prices)
- Primary-key index: Index Scan (daily_prices_pkey)

**Partial index: anomalies, last 90 days**
- Forced sequential scan: Seq Scan (signals)
- idx_signals_anomaly (partial): Bitmap Heap Scan (signals), Bitmap Index Scan (idx_signals_anomaly)

**Materialized view: sector stats, last 12 months**
- Plain view (aggregates every time): 5 scans: Index Only Scan (daily_prices_pkey), Seq Scan (cities), …
- Materialized view (precomputed): Bitmap Heap Scan (mv_sector_daily), Bitmap Index Scan (ux_mv_sector_daily)

**Partitioning: one year, average close per stock**
- Normal table (scans all years): Seq Scan (daily_prices)
- Year-partitioned table (pruned to 1 of 13 partitions): Seq Scan (daily_prices_part_y2025)

## Signal engine

`refresh_signals()` recomputes 30 days of signals for all stocks in **101 ms** (window functions over ~400 days of look-back per stock).

## Table sizes

| Table | Rows (approx.) | Size on disk |
|---|---|---|
| `signals` | 72,675 | 12 MB |
| `daily_prices` | 72,675 | 10 MB |
| `mv_sector_daily` | 14,535 | 2024 kB |
| `pipeline_runs` | 5 | 48 kB |
| `sectors` | 5 | 48 kB |
| `cities` | 9 | 40 kB |
| `companies` | 25 | 40 kB |
| `fundamentals` | 25 | 24 kB |
| `audit_log` | 0 | 24 kB |

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

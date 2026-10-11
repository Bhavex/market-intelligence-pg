-- =====================================================================
-- 06_performance.sql — materialized reporting layer
-- v_sector_daily aggregates ~72k rows on every query; the materialized
-- copy stores the result and is refreshed once after each daily load
-- (src/post_load.py). See docs/BENCHMARKS.md for measured timings.
-- =====================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS mv_sector_daily AS
SELECT * FROM v_sector_daily
WITH DATA;

-- unique index: needed for REFRESH ... CONCURRENTLY (readers never blocked)
CREATE UNIQUE INDEX IF NOT EXISTS ux_mv_sector_daily
    ON mv_sector_daily (price_date, sector_name);

GRANT SELECT ON mv_sector_daily TO readonly_access;

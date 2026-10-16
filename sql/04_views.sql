-- =====================================================================
-- 04_views.sql — reporting views for Power BI / Excel
-- Power BI should connect to these views, not to raw tables.
-- =====================================================================

-- One wide row per company per day: price + signals + sector + location
CREATE OR REPLACE VIEW v_company_daily AS
SELECT p.price_date, c.ticker, c.company_name, sec.sector_name,
       ci.city_name, ci.state, ci.region, ci.latitude, ci.longitude,
       p.open, p.high, p.low, p.close, p.adj_close, p.volume,
       s.return_1d, s.momentum_20d, s.momentum_60d, s.volatility_20d,
       s.sma_50, s.sma_200, s.rsi_14, s.volume_zscore_20d, s.trend,
       s.is_anomaly, s.anomaly_reason
FROM daily_prices p
JOIN companies c   ON c.ticker      = p.ticker
JOIN sectors   sec ON sec.sector_id = c.sector_id
JOIN cities    ci  ON ci.city_id    = c.hq_city_id
LEFT JOIN signals s ON s.ticker = p.ticker AND s.signal_date = p.price_date
WHERE c.in_dashboard;          -- research-only stocks (sql/11_research.sql) never reach the dashboards

-- Latest available row per company (snapshot cards, map, tables)
CREATE OR REPLACE VIEW v_latest_snapshot AS
SELECT DISTINCT ON (ticker) *
FROM v_company_daily
ORDER BY ticker, price_date DESC;

-- Equal-weighted sector performance per day.
-- breakout_count / strong_breakout_count were added at the END so existing columns, order and
-- types are unchanged (CREATE OR REPLACE VIEW; never DROP this view, or grants are lost).
CREATE OR REPLACE VIEW v_sector_daily AS
SELECT v.price_date, v.sector_name,
       COUNT(*)                         AS n_stocks,
       AVG(v.return_1d)                 AS avg_return_1d,
       AVG(v.momentum_20d)              AS avg_momentum_20d,
       AVG(v.volatility_20d)            AS avg_volatility_20d,
       AVG(v.rsi_14)                    AS avg_rsi_14,
       SUM(CASE WHEN v.trend = 'UP'   THEN 1 ELSE 0 END) AS n_uptrend,
       SUM(CASE WHEN v.trend = 'DOWN' THEN 1 ELSE 0 END) AS n_downtrend,
       SUM(CASE WHEN v.is_anomaly     THEN 1 ELSE 0 END) AS n_anomalies,
       COUNT(*) FILTER (WHERE s.breakout_52w)     AS breakout_count,         -- stocks above their 52-week high
       COUNT(*) FILTER (WHERE s.strong_breakout)  AS strong_breakout_count   -- ... on volume z-score > 2
FROM v_company_daily v
LEFT JOIN signals s ON s.ticker = v.ticker AND s.signal_date = v.price_date
GROUP BY v.price_date, v.sector_name;

-- Sector strength: each sector's 20-day momentum vs. the whole universe, ranked per day
CREATE OR REPLACE VIEW v_sector_strength AS
SELECT price_date, sector_name, avg_momentum_20d,
       avg_momentum_20d - AVG(avg_momentum_20d) OVER (PARTITION BY price_date) AS relative_strength,
       RANK() OVER (PARTITION BY price_date ORDER BY avg_momentum_20d DESC NULLS LAST) AS strength_rank
FROM v_sector_daily;

-- Region-level roll-up for the map page
CREATE OR REPLACE VIEW v_region_daily AS
SELECT price_date, region, state,
       COUNT(*)            AS n_stocks,
       AVG(return_1d)      AS avg_return_1d,
       AVG(momentum_20d)   AS avg_momentum_20d
FROM v_company_daily
GROUP BY price_date, region, state;

-- All anomalies, newest first
CREATE OR REPLACE VIEW v_anomalies AS
SELECT price_date, ticker, company_name, sector_name, close,
       return_1d, volume, volume_zscore_20d, anomaly_reason
FROM v_company_daily
WHERE is_anomaly
ORDER BY price_date DESC, ticker;

-- Pipeline health: last run of each job + data freshness
CREATE OR REPLACE VIEW v_pipeline_health AS
SELECT j.job_name, j.started_at, j.finished_at, j.status, j.rows_upserted,
       j.tickers_failed, j.message,
       (SELECT MAX(price_date) FROM daily_prices) AS latest_price_date
FROM (
    SELECT DISTINCT ON (job_name) *
    FROM pipeline_runs
    ORDER BY job_name, started_at DESC
) j;

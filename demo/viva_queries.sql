-- =====================================================================
-- viva_queries.sql — demo queries for the project viva
-- Paste any one query into the Neon SQL Editor and click Run.
-- (These are read-only; they don't change any data.)
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1. Simple JOIN across 3 tables: every company with its sector and HQ city
-- ---------------------------------------------------------------------
SELECT c.ticker, c.company_name, s.sector_name, ci.city_name, ci.state, ci.region
FROM companies c
JOIN sectors s  ON s.sector_id = c.sector_id
JOIN cities  ci ON ci.city_id  = c.hq_city_id
WHERE c.is_active
ORDER BY s.sector_name, c.ticker;


-- ---------------------------------------------------------------------
-- 2. GROUP BY + HAVING: regions with more than 3 company headquarters
-- ---------------------------------------------------------------------
SELECT ci.region, COUNT(*) AS companies
FROM companies c
JOIN cities ci ON ci.city_id = c.hq_city_id
GROUP BY ci.region
HAVING COUNT(*) > 3
ORDER BY companies DESC;


-- ---------------------------------------------------------------------
-- 3. Aggregates: yearly average close and total volume for one stock
-- ---------------------------------------------------------------------
SELECT EXTRACT(YEAR FROM price_date) AS year,
       ROUND(AVG(close), 2)          AS avg_close,
       MIN(low)                      AS year_low,
       MAX(high)                     AS year_high,
       SUM(volume)                   AS total_volume
FROM daily_prices
WHERE ticker = 'INFY.NS'
GROUP BY year
ORDER BY year;


-- ---------------------------------------------------------------------
-- 4. Subquery: stocks whose latest close is above their own 5-year average
-- ---------------------------------------------------------------------
SELECT p.ticker, p.close AS latest_close
FROM daily_prices p
WHERE p.price_date = (SELECT MAX(price_date) FROM daily_prices)
  AND p.close > (SELECT AVG(p2.close)
                 FROM daily_prices p2
                 WHERE p2.ticker = p.ticker                 -- correlated subquery
                   AND p2.price_date >= CURRENT_DATE - INTERVAL '5 years')
ORDER BY p.ticker;


-- ---------------------------------------------------------------------
-- 5. Window function RANK(): best and worst 20-day momentum within each sector
-- ---------------------------------------------------------------------
SELECT sector_name, ticker, ROUND(momentum_20d * 100, 2) AS momentum_pct,
       RANK() OVER (PARTITION BY sector_name ORDER BY momentum_20d DESC) AS rank_in_sector
FROM v_latest_snapshot
ORDER BY sector_name, rank_in_sector;


-- ---------------------------------------------------------------------
-- 6. Window function LAG(): month-on-month return for one stock
-- ---------------------------------------------------------------------
WITH monthly AS (
    SELECT DISTINCT ON (date_trunc('month', price_date))
           date_trunc('month', price_date)::date AS month,
           close
    FROM daily_prices
    WHERE ticker = 'TCS.NS'
    ORDER BY date_trunc('month', price_date), price_date DESC   -- last close of each month
)
SELECT month, close,
       ROUND((close / LAG(close) OVER (ORDER BY month) - 1) * 100, 2) AS mom_return_pct
FROM monthly
ORDER BY month DESC
LIMIT 12;


-- ---------------------------------------------------------------------
-- 7. CTE + window: running yearly-high ("all-time high so far") and drawdown
-- ---------------------------------------------------------------------
WITH px AS (
    SELECT price_date, close,
           MAX(close) OVER (ORDER BY price_date) AS running_peak
    FROM daily_prices
    WHERE ticker = 'HDFCBANK.NS'
)
SELECT price_date, close, running_peak,
       ROUND((close / running_peak - 1) * 100, 2) AS drawdown_pct
FROM px
ORDER BY drawdown_pct
LIMIT 5;                                   -- 5 worst drawdowns since 2015


-- ---------------------------------------------------------------------
-- 8. Sector strength today (uses the reporting view)
-- ---------------------------------------------------------------------
SELECT sector_name, strength_rank,
       ROUND(avg_momentum_20d * 100, 2)  AS momentum_pct,
       ROUND(relative_strength * 100, 2) AS vs_market_pct
FROM v_sector_strength
WHERE price_date = (SELECT MAX(price_date) FROM daily_prices)
ORDER BY strength_rank;


-- ---------------------------------------------------------------------
-- 9. Anomalies in the last 30 days, with the move size
-- ---------------------------------------------------------------------
SELECT price_date, ticker, sector_name, anomaly_reason,
       ROUND(return_1d * 100, 2) AS move_pct,
       ROUND(volume_zscore_20d, 1) AS volume_z
FROM v_anomalies
WHERE price_date >= CURRENT_DATE - 30
ORDER BY price_date DESC;


-- ---------------------------------------------------------------------
-- 10. Correlation between two sectors' daily returns (statistical aggregate)
-- ---------------------------------------------------------------------
SELECT ROUND(CORR(it.avg_return_1d, bank.avg_return_1d)::numeric, 3) AS it_vs_banking_correlation
FROM v_sector_daily it
JOIN v_sector_daily bank ON bank.price_date = it.price_date
WHERE it.sector_name = 'IT' AND bank.sector_name = 'Banking';


-- ---------------------------------------------------------------------
-- 11. EXISTS / NOT EXISTS: companies with no fundamentals snapshot yet
-- ---------------------------------------------------------------------
SELECT c.ticker, c.company_name
FROM companies c
WHERE c.is_active
  AND NOT EXISTS (SELECT 1 FROM fundamentals f WHERE f.ticker = c.ticker);


-- ---------------------------------------------------------------------
-- 12. Valuation table: latest P/E vs sector average (join + window AVG)
-- ---------------------------------------------------------------------
WITH latest AS (
    SELECT DISTINCT ON (ticker) ticker, pe_ratio, market_cap
    FROM fundamentals
    ORDER BY ticker, snapshot_date DESC
)
SELECT s.sector_name, l.ticker, ROUND(l.pe_ratio, 1) AS pe,
       ROUND(AVG(l.pe_ratio) OVER (PARTITION BY s.sector_name), 1) AS sector_avg_pe,
       ROUND(l.market_cap / 1e7, 0) AS market_cap_crore
FROM latest l
JOIN companies c ON c.ticker = l.ticker
JOIN sectors s   ON s.sector_id = c.sector_id
ORDER BY s.sector_name, pe DESC NULLS LAST;


-- ---------------------------------------------------------------------
-- 13. Pipeline monitoring: is the data fresh?
-- ---------------------------------------------------------------------
SELECT * FROM v_pipeline_health;


-- ---------------------------------------------------------------------
-- 14. Audit trail: what changed recently (filled by triggers)
-- ---------------------------------------------------------------------
SELECT * FROM v_recent_changes LIMIT 20;

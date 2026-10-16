-- =====================================================================
-- Sector-Wise Market Intelligence & Signal Dashboard
-- Person B: Analytics & Signal Validation — SQL queries used
-- Database: PostgreSQL 17 (Neon), read-only login: powerbi_reader
-- =====================================================================


-- ---------------------------------------------------------------------
-- TASK 1: Data accuracy check
-- Picks one random active stock per sector and 5 random trading days
-- on which all 5 stocks traded; returns stored prices for comparison
-- against Yahoo Finance / NSE.
-- (Random sample: re-running gives different stocks/dates.)
-- ---------------------------------------------------------------------
WITH picked_stocks AS MATERIALIZED (
  SELECT DISTINCT ON (c.sector_id) c.ticker, c.company_name, s.sector_name
  FROM companies c
  JOIN sectors s USING (sector_id)
  WHERE c.is_active AND c.in_dashboard
  ORDER BY c.sector_id, random()
),
picked_dates AS MATERIALIZED (
  SELECT price_date
  FROM daily_prices
  WHERE ticker IN (SELECT ticker FROM picked_stocks)
  GROUP BY price_date
  HAVING COUNT(*) = 5          -- all 5 stocks traded that day
  ORDER BY random()
  LIMIT 5
)
SELECT ps.sector_name, dp.ticker, ps.company_name,
       dp.price_date, dp.open, dp.high, dp.low, dp.close, dp.adj_close
FROM daily_prices dp
JOIN picked_stocks ps USING (ticker)
WHERE dp.price_date IN (SELECT price_date FROM picked_dates)
ORDER BY dp.ticker, dp.price_date;


-- ---------------------------------------------------------------------
-- TASK 2: Signal validation — last 100 trading days for one stock
-- Run once per stock (ICICIBANK.NS, then MARUTI.NS); momentum_20d,
-- sma_50 and rsi_14 were then recomputed by hand in the spreadsheet.
-- ---------------------------------------------------------------------
SELECT *
FROM (
  SELECT p.ticker, p.price_date, p.close, p.adj_close,
         s.momentum_20d, s.sma_50, s.rsi_14
  FROM daily_prices p
  LEFT JOIN signals s
         ON s.ticker = p.ticker AND s.signal_date = p.price_date
  WHERE p.ticker = 'ICICIBANK.NS'          -- change to 'MARUTI.NS' for stock 2
  ORDER BY p.price_date DESC
  LIMIT 100
) t
ORDER BY price_date;


-- ---------------------------------------------------------------------
-- TASK 3a: Strongest sector each calendar year (2015–2026)
-- Compounds daily equal-weighted sector returns into an annual return
-- and ranks the 5 sectors within each year. 2026 is year-to-date.
-- ---------------------------------------------------------------------
WITH yearly AS (
  SELECT EXTRACT(YEAR FROM price_date)::int AS year,
         sector_name,
         EXP(SUM(LN(1 + avg_return_1d))) - 1 AS annual_return
  FROM v_sector_daily
  WHERE avg_return_1d IS NOT NULL
  GROUP BY 1, 2
)
SELECT year,
       sector_name,
       ROUND((annual_return * 100)::numeric, 2) AS return_pct,
       RANK() OVER (PARTITION BY year ORDER BY annual_return DESC) AS rank_in_year
FROM yearly
ORDER BY year, rank_in_year;


-- ---------------------------------------------------------------------
-- TASK 3b: Most / least volatile sector (2015–2026)
-- annual_volatility_pct    = stdev of daily sector returns x sqrt(252)
-- avg_stock_volatility_pct = average of the stocks' own 20-day volatility
-- ---------------------------------------------------------------------
SELECT sector_name,
       ROUND((STDDEV_SAMP(avg_return_1d) * SQRT(252) * 100)::numeric, 2) AS annual_volatility_pct,
       ROUND((AVG(avg_volatility_20d) * 100)::numeric, 2)               AS avg_stock_volatility_pct,
       ROUND((MIN(avg_return_1d) * 100)::numeric, 2)                    AS worst_day_pct,
       ROUND((MAX(avg_return_1d) * 100)::numeric, 2)                    AS best_day_pct
FROM v_sector_daily
WHERE avg_return_1d IS NOT NULL
GROUP BY sector_name
ORDER BY annual_volatility_pct DESC;


-- ---------------------------------------------------------------------
-- TASK 3c: 5x5 correlation matrix of sector daily returns
-- ---------------------------------------------------------------------
WITH r AS (
  SELECT price_date, sector_name, avg_return_1d AS ret
  FROM v_sector_daily
  WHERE avg_return_1d IS NOT NULL
)
SELECT a.sector_name AS sector,
  ROUND((CORR(a.ret, b.ret) FILTER (WHERE b.sector_name = 'Auto'))::numeric, 3)    AS "Auto",
  ROUND((CORR(a.ret, b.ret) FILTER (WHERE b.sector_name = 'Banking'))::numeric, 3) AS "Banking",
  ROUND((CORR(a.ret, b.ret) FILTER (WHERE b.sector_name = 'FMCG'))::numeric, 3)    AS "FMCG",
  ROUND((CORR(a.ret, b.ret) FILTER (WHERE b.sector_name = 'IT'))::numeric, 3)      AS "IT",
  ROUND((CORR(a.ret, b.ret) FILTER (WHERE b.sector_name = 'Pharma'))::numeric, 3)  AS "Pharma"
FROM r a
JOIN r b ON a.price_date = b.price_date
GROUP BY a.sector_name
ORDER BY a.sector_name;


-- ---------------------------------------------------------------------
-- TASK 4: Largest anomalies (for news case studies)
-- Top 20 flagged anomaly days by size of the daily move (adj_close-based,
-- so splits/bonuses are not counted as moves). 5 were picked from
-- different dates/stocks for the case studies.
-- ---------------------------------------------------------------------
SELECT s.signal_date,
       s.ticker,
       c.company_name,
       sec.sector_name,
       ROUND((s.return_1d * 100)::numeric, 2)      AS move_pct,
       ROUND(s.volume_zscore_20d::numeric, 1)      AS volume_zscore,
       s.anomaly_reason
FROM signals s
JOIN companies c   ON c.ticker = s.ticker
JOIN sectors   sec ON sec.sector_id = c.sector_id
WHERE s.is_anomaly AND c.in_dashboard
ORDER BY ABS(s.return_1d) DESC NULLS LAST
LIMIT 20;


-- ---------------------------------------------------------------------
-- TASK 5: Monthly backtest — hold the top-ranked sector next month
--         vs equal-weight all 5 sectors; growth of Rs 100
-- Signal: highest relative_strength on the last trading day of month M
-- is held for the whole of month M+1 (no look-ahead). No costs.
-- ---------------------------------------------------------------------
WITH monthly AS (            -- each sector's return for each month
  SELECT date_trunc('month', price_date)::date AS month, sector_name,
         EXP(SUM(LN(1 + avg_return_1d))) - 1 AS m_ret
  FROM v_sector_daily
  WHERE avg_return_1d IS NOT NULL
  GROUP BY 1, 2
),
last_day AS (                -- last trading day of each month
  SELECT date_trunc('month', price_date)::date AS month, MAX(price_date) AS d
  FROM v_sector_strength
  WHERE relative_strength IS NOT NULL
  GROUP BY 1
),
pick AS (                    -- strongest sector on that day
  SELECT DISTINCT ON (l.month)
         (l.month + INTERVAL '1 month')::date AS hold_month,
         s.sector_name AS picked
  FROM last_day l
  JOIN v_sector_strength s ON s.price_date = l.d
  WHERE s.relative_strength IS NOT NULL
  ORDER BY l.month, s.relative_strength DESC
),
ew AS (                      -- equal-weight: average of all 5 sectors
  SELECT month, AVG(m_ret) AS ew_ret FROM monthly GROUP BY month
),
combined AS (
  SELECT p.hold_month AS month, p.picked, m.m_ret AS strat_ret, e.ew_ret
  FROM pick p
  JOIN monthly m ON m.month = p.hold_month AND m.sector_name = p.picked
  JOIN ew e      ON e.month = p.hold_month
)
SELECT month,
       picked AS sector_held,
       ROUND((strat_ret * 100)::numeric, 2) AS strategy_ret_pct,
       ROUND((ew_ret   * 100)::numeric, 2) AS equal_wt_ret_pct,
       ROUND((100 * EXP(SUM(LN(1 + strat_ret)) OVER (ORDER BY month)))::numeric, 2) AS strategy_value_rs,
       ROUND((100 * EXP(SUM(LN(1 + ew_ret))   OVER (ORDER BY month)))::numeric, 2) AS equal_wt_value_rs
FROM combined
ORDER BY month;


-- ---------------------------------------------------------------------
-- TASK 6: Valuation snapshot — P/E and P/B vs sector median
-- Uses the latest weekly fundamentals snapshot per stock.
-- Cheap: P/E < 80% of sector median; Expensive: > 120%; else Fair.
-- ---------------------------------------------------------------------
WITH latest AS (
  SELECT DISTINCT ON (ticker) *
  FROM fundamentals
  ORDER BY ticker, snapshot_date DESC
),
base AS (
  SELECT sec.sector_name, c.ticker, c.company_name, l.snapshot_date,
         l.pe_ratio, l.price_to_book, l.dividend_yield, l.market_cap
  FROM latest l
  JOIN companies c   ON c.ticker = l.ticker
  JOIN sectors   sec ON sec.sector_id = c.sector_id
  WHERE c.is_active AND c.in_dashboard
),
med AS (
  SELECT sector_name,
         PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pe_ratio)
           FILTER (WHERE pe_ratio > 0)      AS sector_median_pe,
         PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY price_to_book)
           FILTER (WHERE price_to_book > 0) AS sector_median_pb
  FROM base
  GROUP BY sector_name
)
SELECT b.sector_name, b.ticker, b.company_name, b.snapshot_date,
       ROUND(b.pe_ratio::numeric, 1)           AS pe,
       ROUND(m.sector_median_pe::numeric, 1)   AS sector_median_pe,
       ROUND(((b.pe_ratio / m.sector_median_pe - 1) * 100)::numeric, 0) AS pe_vs_sector_pct,
       ROUND(b.price_to_book::numeric, 2)      AS pb,
       ROUND(m.sector_median_pb::numeric, 2)   AS sector_median_pb,
       ROUND(b.dividend_yield::numeric, 2)     AS dividend_yield,
       CASE
         WHEN b.pe_ratio IS NULL OR b.pe_ratio <= 0     THEN 'n/a'
         WHEN b.pe_ratio < 0.8 * m.sector_median_pe     THEN 'Cheap'
         WHEN b.pe_ratio > 1.2 * m.sector_median_pe     THEN 'Expensive'
         ELSE 'Fair'
       END AS valuation_flag
FROM base b
JOIN med m ON m.sector_name = b.sector_name
ORDER BY b.sector_name, b.pe_ratio;

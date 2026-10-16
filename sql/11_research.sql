-- =====================================================================
-- 11_research.sql — second free feed and data quality by company size
--
-- * alt_feed_prices: Yahoo's BSE listing (SYMBOL.BO), checked against the official BSE file the
--   same way Yahoo's NSE listing is checked against NSE (sql/09). first_close keeps the value as
--   first received, so first-print accuracy can be measured for this feed too.
-- * v_quality_by_group: every headline measure for three groups of stocks:
--     dashboard = the 25 stocks on the site, large = + NIFTY 50, small = 25 NIFTY SMALLCAP 100 stocks
--   (src/universe.py builds the research universe).
-- Safe to re-run.
-- =====================================================================

CREATE TABLE IF NOT EXISTS alt_feed_prices (
    feed          VARCHAR(16)   NOT NULL CHECK (feed IN ('yahoo_bse')),
    ticker        VARCHAR(20)   NOT NULL REFERENCES companies(ticker) ON UPDATE CASCADE,
    price_date    DATE          NOT NULL,
    close         NUMERIC(14,4) NOT NULL CHECK (close > 0),
    adj_close     NUMERIC(14,4),
    volume        BIGINT        CHECK (volume >= 0),
    first_close   NUMERIC(14,4) NOT NULL,             -- as first received (first print)
    first_seen    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    last_changed  TIMESTAMPTZ   NOT NULL DEFAULT now(),
    n_changes     INTEGER       NOT NULL DEFAULT 0,   -- how often the close/volume was revised later
    run_id        BIGINT,
    PRIMARY KEY (feed, ticker, price_date)
);

-- Second feed vs the official BSE close (same agreement rule as v_source_agreement)
CREATE OR REPLACE VIEW v_feed_agreement AS
SELECT f.feed, f.ticker, f.price_date,
       f.close                                    AS feed_close,
       r.close                                    AS official_close,
       ROUND((f.close / r.close - 1) * 10000, 3)  AS diff_bp,
       ABS(f.close - r.close) <= GREATEST(0.05, r.close * 0.0001)       AS agrees,
       ABS(f.first_close - r.close) <= GREATEST(0.05, r.close * 0.0001) AS first_print_agrees,
       (f.first_seen - session_final_at(f.price_date)) < INTERVAL '3 days' AS live_captured,
       (f.close / r.close NOT BETWEEN 0.8 AND 1.25) AS scale_mismatch,
       f.n_changes
FROM alt_feed_prices f
JOIN reference_prices r ON r.source = 'BSE' AND r.ticker = f.ticker AND r.price_date = f.price_date;

-- Headline data-quality measures by group of stocks
CREATE OR REPLACE VIEW v_quality_by_group AS
WITH g AS (
    SELECT ticker, 'dashboard'::text AS grp FROM companies WHERE is_active AND in_dashboard
    UNION ALL
    SELECT ticker, 'large' FROM companies WHERE is_active AND cap_group = 'large'
    UNION ALL
    SELECT ticker, 'small' FROM companies WHERE is_active AND cap_group = 'small'
),
days AS (
    SELECT ticker, COUNT(*) AS n_days, MIN(price_date) AS first_day FROM daily_prices GROUP BY ticker
),
nse AS (            -- Yahoo .NS vs the official NSE close
    SELECT ticker, COUNT(*) AS n, COUNT(*) FILTER (WHERE agrees) AS n_agree,
           COUNT(*) FILTER (WHERE live_captured) AS n_live,
           COUNT(*) FILTER (WHERE live_captured AND first_print_agrees) AS n_live_agree
    FROM v_source_agreement WHERE source = 'NSE' AND NOT scale_mismatch GROUP BY ticker
),
bse AS (            -- Yahoo .BO vs the official BSE close
    SELECT ticker, COUNT(*) AS n, COUNT(*) FILTER (WHERE agrees) AS n_agree,
           COUNT(*) FILTER (WHERE live_captured) AS n_live,
           COUNT(*) FILTER (WHERE live_captured AND first_print_agrees) AS n_live_agree
    FROM v_feed_agreement WHERE NOT scale_mismatch GROUP BY ticker
),
miss AS (           -- sessions in which NSE says the stock traded but Yahoo has no row
    SELECT r.ticker, COUNT(*) AS n_traded, COUNT(*) FILTER (WHERE p.ticker IS NULL) AS n_missing
    FROM reference_prices r
    JOIN days d ON d.ticker = r.ticker AND r.price_date >= d.first_day
    LEFT JOIN daily_prices p ON p.ticker = r.ticker AND p.price_date = r.price_date
    WHERE r.source = 'NSE'
    GROUP BY r.ticker
),
rev AS (
    SELECT ticker,
           COUNT(*) FILTER (WHERE revision_kind NOT IN ('dividend_adjustment', 'split_or_bonus_adjustment', 'deleted')) AS n_value_rev,
           COUNT(*) FILTER (WHERE revision_kind IN ('dividend_adjustment', 'split_or_bonus_adjustment')) AS n_rescaled
    FROM v_price_revisions GROUP BY ticker
)
SELECT g.grp,
       COUNT(*)                                                         AS n_stocks,
       SUM(days.n_days)                                                 AS n_stock_days,
       SUM(nse.n)                                                       AS nse_compared,
       ROUND(100.0 * SUM(nse.n_agree) / NULLIF(SUM(nse.n), 0), 3)       AS nse_agree_pct,
       SUM(nse.n_live)                                                  AS nse_live_first_prints,
       ROUND(100.0 * SUM(nse.n_live_agree) / NULLIF(SUM(nse.n_live), 0), 2) AS nse_first_print_agree_pct,
       SUM(bse.n)                                                       AS bse_feed_compared,
       ROUND(100.0 * SUM(bse.n_agree) / NULLIF(SUM(bse.n), 0), 3)       AS bse_feed_agree_pct,
       SUM(bse.n_live)                                                  AS bse_feed_live_first_prints,
       ROUND(100.0 * SUM(bse.n_live_agree) / NULLIF(SUM(bse.n_live), 0), 2) AS bse_feed_first_print_agree_pct,
       SUM(miss.n_traded)                                               AS sessions_traded,
       SUM(miss.n_missing)                                              AS sessions_missing_in_yahoo,
       ROUND(100.0 * SUM(miss.n_missing) / NULLIF(SUM(miss.n_traded), 0), 3) AS missing_pct,
       ROUND(1000.0 * COALESCE(SUM(rev.n_value_rev), 0) / NULLIF(SUM(days.n_days), 0), 3) AS value_revisions_per_1000,
       COALESCE(SUM(rev.n_rescaled), 0)                                 AS rescaled_rows
FROM g
LEFT JOIN days USING (ticker)
LEFT JOIN nse  USING (ticker)
LEFT JOIN bse  USING (ticker)
LEFT JOIN miss USING (ticker)
LEFT JOIN rev  USING (ticker)
GROUP BY g.grp;

-- Database size, so storage on the free plan can be watched (1 GB per project on Neon Free)
CREATE OR REPLACE VIEW v_storage AS
SELECT pg_size_pretty(pg_database_size(current_database())) AS database_size,
       pg_database_size(current_database())                AS database_bytes,
       (SELECT COUNT(*) FROM price_versions)               AS price_versions_rows,
       (SELECT COUNT(*) FROM audit_log)                    AS audit_log_rows;

GRANT SELECT ON alt_feed_prices, v_feed_agreement, v_quality_by_group, v_storage TO readonly_access;

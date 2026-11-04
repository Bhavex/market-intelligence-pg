CREATE TABLE IF NOT EXISTS rejected_prices (
    rejection_id  BIGSERIAL    PRIMARY KEY,
    run_id        BIGINT       REFERENCES pipeline_runs(run_id) ON DELETE SET NULL,
    ticker        VARCHAR(20)  NOT NULL,
    price_date    DATE,
    check_name    VARCHAR(40)  NOT NULL,
    detail        TEXT         NOT NULL,
    raw_row       JSONB,
    rejected_at   TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_rejected_prices_ticker_date ON rejected_prices (ticker, price_date);
CREATE INDEX IF NOT EXISTS idx_rejected_prices_run ON rejected_prices (run_id);

CREATE TABLE IF NOT EXISTS states (
    state   VARCHAR(60) PRIMARY KEY,
    region  VARCHAR(10) NOT NULL CHECK (region IN ('North','South','East','West','Central')),
    UNIQUE (state, region)
);

INSERT INTO states (state, region)
SELECT DISTINCT ON (state) state, region FROM cities ORDER BY state, region
ON CONFLICT (state) DO NOTHING;

DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN
        SELECT * FROM (VALUES
            ('daily_prices',   'daily_prices_open_pos_chk',     'CHECK (open IS NULL OR open > 0)'),
            ('daily_prices',   'daily_prices_high_pos_chk',     'CHECK (high IS NULL OR high > 0)'),
            ('daily_prices',   'daily_prices_low_pos_chk',      'CHECK (low IS NULL OR low > 0)'),
            ('daily_prices',   'daily_prices_adj_pos_chk',      'CHECK (adj_close IS NULL OR adj_close > 0)'),
            ('daily_prices',   'daily_prices_date_range_chk',   'CHECK (price_date >= DATE ''1990-01-01'')'),
            ('signals',        'signals_rsi_range_chk',         'CHECK (rsi_14 IS NULL OR rsi_14 BETWEEN 0 AND 100)'),
            ('signals',        'signals_volatility_nonneg_chk', 'CHECK (volatility_20d IS NULL OR volatility_20d >= 0)'),
            ('signals',        'signals_anomaly_reason_chk',    'CHECK (is_anomaly = (anomaly_reason IS NOT NULL))'),
            ('fundamentals',   'fundamentals_nonneg_chk',       'CHECK ((market_cap IS NULL OR market_cap >= 0) AND (dividend_yield IS NULL OR dividend_yield >= 0))'),
            ('pipeline_runs',  'pipeline_runs_times_chk',       'CHECK (finished_at IS NULL OR finished_at >= started_at)'),
            ('pipeline_runs',  'pipeline_runs_rows_nonneg_chk', 'CHECK (rows_upserted >= 0)'),
            ('companies',      'companies_ticker_upper_chk',    'CHECK (ticker = upper(ticker))'),
            ('price_versions', 'price_versions_ticker_fk',      'FOREIGN KEY (ticker) REFERENCES companies(ticker) ON UPDATE CASCADE'),
            ('cities',         'cities_state_region_fk',        'FOREIGN KEY (state, region) REFERENCES states(state, region) ON UPDATE CASCADE DEFERRABLE INITIALLY DEFERRED')
        ) AS t(tbl, name, def)
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = c.name) THEN
            EXECUTE format('ALTER TABLE %I ADD CONSTRAINT %I %s NOT VALID', c.tbl, c.name, c.def);
        END IF;
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = c.name AND NOT convalidated) THEN
            BEGIN
                EXECUTE format('ALTER TABLE %I VALIDATE CONSTRAINT %I', c.tbl, c.name);
            EXCEPTION WHEN check_violation OR foreign_key_violation THEN
                RAISE NOTICE '% stays NOT VALID: existing rows violate it (%); new and updated rows are still checked',
                             c.name, SQLERRM;
            END;
        END IF;
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_signals_breakout       ON signals (signal_date) WHERE breakout_52w;
CREATE INDEX IF NOT EXISTS idx_audit_log_row_key      ON audit_log (table_name, row_key);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_problems ON pipeline_runs (started_at DESC) WHERE status <> 'success';
CREATE INDEX IF NOT EXISTS idx_fundamentals_snapshot  ON fundamentals (snapshot_date);

CREATE OR REPLACE VIEW v_latest_prices_by_sector AS
SELECT sec.sector_name,
       c.ticker,
       c.company_name,
       l.price_date                                                        AS latest_date,
       l.close,
       l.adj_close,
       l.volume,
       l.prev_close,
       ROUND((COALESCE(l.adj_close, l.close) / NULLIF(l.prev_px, 0) - 1) * 100, 2) AS change_pct,
       s.trend,
       s.rsi_14,
       s.momentum_20d,
       s.volatility_20d,
       s.high_52w,
       s.pct_from_52w_high,
       s.breakout_52w,
       s.is_anomaly,
       l.price_date = (SELECT MAX(price_date) FROM daily_prices)           AS is_current,
       RANK() OVER (PARTITION BY sec.sector_name
                    ORDER BY COALESCE(l.adj_close, l.close) / NULLIF(l.prev_px, 0) DESC NULLS LAST) AS rank_in_sector
FROM companies c
JOIN sectors sec ON sec.sector_id = c.sector_id
CROSS JOIN LATERAL (
    SELECT p.price_date, p.close, p.adj_close, p.volume,
           prev.close                          AS prev_close,
           COALESCE(prev.adj_close, prev.close) AS prev_px
    FROM daily_prices p
    LEFT JOIN LATERAL (
        SELECT p2.close, p2.adj_close FROM daily_prices p2
        WHERE p2.ticker = p.ticker AND p2.price_date < p.price_date
        ORDER BY p2.price_date DESC LIMIT 1
    ) prev ON TRUE
    WHERE p.ticker = c.ticker
    ORDER BY p.price_date DESC
    LIMIT 1
) l
LEFT JOIN signals s ON s.ticker = c.ticker AND s.signal_date = l.price_date
WHERE c.is_active AND c.in_dashboard;

CREATE OR REPLACE VIEW v_sector_daily_performance AS
WITH r AS (
    SELECT p.price_date, sec.sector_name, p.ticker, p.close, p.volume,
           COALESCE(p.adj_close, p.close)
             / NULLIF(LAG(COALESCE(p.adj_close, p.close)) OVER (PARTITION BY p.ticker ORDER BY p.price_date), 0) - 1 AS ret
    FROM daily_prices p
    JOIN companies c   ON c.ticker = p.ticker
    JOIN sectors   sec ON sec.sector_id = c.sector_id
    WHERE c.in_dashboard
),
d AS (
    SELECT price_date, sector_name,
           COUNT(*)                                                         AS n_stocks,
           COUNT(*) FILTER (WHERE ret > 0)                                  AS advancers,
           COUNT(*) FILTER (WHERE ret < 0)                                  AS decliners,
           AVG(ret)                                                         AS avg_ret,
           PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ret::float8)         AS median_ret,
           MAX(ret)                                                         AS best_ret,
           MIN(ret)                                                         AS worst_ret,
           (ARRAY_AGG(ticker ORDER BY ret DESC NULLS LAST))[1]              AS best_ticker,
           (ARRAY_AGG(ticker ORDER BY ret ASC  NULLS LAST))[1]              AS worst_ticker,
           SUM(volume)                                                      AS total_volume,
           SUM(close * volume)                                              AS turnover_inr
    FROM r
    GROUP BY price_date, sector_name
)
SELECT price_date,
       sector_name,
       n_stocks,
       advancers,
       decliners,
       ROUND(avg_ret, 6)                                                    AS avg_return_1d,
       ROUND(median_ret::numeric, 6)                                        AS median_return_1d,
       ROUND(best_ret, 6)                                                   AS best_return_1d,
       best_ticker,
       ROUND(worst_ret, 6)                                                  AS worst_return_1d,
       worst_ticker,
       total_volume,
       ROUND(turnover_inr, 2)                                               AS turnover_inr,
       ROUND(100 * EXP(SUM(LN(GREATEST(1 + COALESCE(avg_ret, 0), 0.000001)))
                       OVER (PARTITION BY sector_name ORDER BY price_date)), 4) AS sector_index
FROM d;

CREATE OR REPLACE VIEW v_sector_returns_summary AS
WITH x AS (
    SELECT sector_name, price_date, n_stocks, advancers, decliners, avg_return_1d, sector_index,
           LAG(sector_index, 5)  OVER w AS idx_5,
           LAG(sector_index, 21) OVER w AS idx_21,
           LAG(sector_index, 63) OVER w AS idx_63,
           FIRST_VALUE(sector_index / (1 + COALESCE(avg_return_1d, 0)))
               OVER (PARTITION BY sector_name, EXTRACT(YEAR FROM price_date) ORDER BY price_date) AS idx_year_start
    FROM v_sector_daily_performance
    WINDOW w AS (PARTITION BY sector_name ORDER BY price_date)
)
SELECT sector_name,
       price_date                                         AS as_of,
       n_stocks,
       advancers,
       decliners,
       ROUND(avg_return_1d * 100, 2)                      AS return_1d_pct,
       ROUND((sector_index / NULLIF(idx_5, 0)  - 1) * 100, 2) AS return_1w_pct,
       ROUND((sector_index / NULLIF(idx_21, 0) - 1) * 100, 2) AS return_1m_pct,
       ROUND((sector_index / NULLIF(idx_63, 0) - 1) * 100, 2) AS return_3m_pct,
       ROUND((sector_index / NULLIF(idx_year_start, 0) - 1) * 100, 2) AS return_ytd_pct,
       sector_index,
       RANK() OVER (ORDER BY sector_index / NULLIF(idx_21, 0) DESC NULLS LAST) AS rank_1m
FROM x
WHERE price_date = (SELECT MAX(price_date) FROM v_sector_daily_performance);

CREATE OR REPLACE VIEW v_rejections_summary AS
SELECT rejected_at::date  AS rejected_on,
       check_name,
       COUNT(*)            AS n_rows,
       COUNT(DISTINCT ticker) AS n_tickers,
       MIN(price_date)     AS first_price_date,
       MAX(price_date)     AS last_price_date
FROM rejected_prices
GROUP BY rejected_at::date, check_name;

GRANT SELECT ON rejected_prices, states, v_latest_prices_by_sector, v_sector_daily_performance,
                v_sector_returns_summary, v_rejections_summary TO readonly_access;

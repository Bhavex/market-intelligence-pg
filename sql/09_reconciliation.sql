-- =====================================================================
-- 09_reconciliation.sql — check the free feed against the exchanges' own files
--
-- Yahoo Finance is free but unofficial. NSE and BSE publish an official end-of-day file
-- ("bhavcopy", UDiFF format since July 2024) for every session. src/official_prices.py
-- downloads it into reference_prices, and the views below measure how often, and by how much,
-- the free feed disagrees with the official record — at first print and after it settles.
-- Safe to re-run.
-- =====================================================================

-- ISIN = the exchange-independent security id; learned from the NSE file, used to match BSE rows.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'companies' AND column_name = 'isin') THEN
        ALTER TABLE companies ADD COLUMN isin VARCHAR(12);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS reference_prices (
    source       VARCHAR(8)    NOT NULL CHECK (source IN ('NSE', 'BSE')),
    ticker       VARCHAR(20)   NOT NULL REFERENCES companies(ticker) ON UPDATE CASCADE,
    price_date   DATE          NOT NULL,
    open         NUMERIC(14,4),
    high         NUMERIC(14,4),
    low          NUMERIC(14,4),
    close        NUMERIC(14,4) NOT NULL CHECK (close > 0),   -- official closing price
    last_price   NUMERIC(14,4),                             -- last traded price (differs from close)
    prev_close   NUMERIC(14,4),
    volume       BIGINT        CHECK (volume >= 0),
    n_trades     BIGINT,
    isin         VARCHAR(12),
    fetched_at   TIMESTAMPTZ   NOT NULL DEFAULT now(),
    run_id       BIGINT,
    PRIMARY KEY (source, ticker, price_date),
    CHECK (high IS NULL OR low IS NULL OR high >= low)
);
CREATE INDEX IF NOT EXISTS idx_reference_prices_key ON reference_prices (ticker, price_date);

-- What happened to each attempted download (so the job knows what is still missing, and the
-- report can state data availability honestly).
CREATE TABLE IF NOT EXISTS reference_fetch_log (
    source        VARCHAR(8)  NOT NULL CHECK (source IN ('NSE', 'BSE')),
    file_date     DATE        NOT NULL,
    status        VARCHAR(14) NOT NULL CHECK (status IN ('ok', 'not_published', 'blocked', 'error')),
    http_status   INTEGER,
    rows_matched  INTEGER     NOT NULL DEFAULT 0,
    detail        TEXT,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source, file_date)
);

-- ---------- Yahoo vs official, row by row ----------
-- Agreement rule: within one tick (Rs 0.05) or 1 basis point, whichever is larger.
-- first_print_* compares the FIRST version Yahoo gave us (from price_versions) with the official
-- close; live_captured marks rows whose first version was recorded within 3 days of the session,
-- i.e. a real first print and not part of the one-time historical backfill.
CREATE OR REPLACE VIEW v_source_agreement AS
WITH first_print AS (
    SELECT DISTINCT ON (ticker, price_date)
           ticker, price_date, close AS first_close, sys_from AS first_seen
    FROM price_versions
    ORDER BY ticker, price_date, sys_from
)
SELECT r.source, p.ticker, p.price_date,
       p.close                                   AS yahoo_close,
       r.close                                   AS official_close,
       r.last_price                              AS official_last_price,
       ROUND((p.close / r.close - 1) * 10000, 3) AS diff_bp,
       ABS(p.close - r.close) <= GREATEST(0.05, r.close * 0.0001) AS agrees,
       ABS(p.close - r.last_price) <= GREATEST(0.05, r.last_price * 0.0001)
           AND NOT (ABS(p.close - r.close) <= GREATEST(0.05, r.close * 0.0001))
                                                 AS matches_last_trade_instead,
       p.volume                                  AS yahoo_volume,
       r.volume                                  AS official_volume,
       ROUND((p.volume::numeric / NULLIF(r.volume, 0) - 1) * 100, 2) AS volume_diff_pct,
       f.first_close                             AS first_print_close,
       ROUND((f.first_close / r.close - 1) * 10000, 3) AS first_print_diff_bp,
       ABS(f.first_close - r.close) <= GREATEST(0.05, r.close * 0.0001) AS first_print_agrees,
       (f.first_seen - session_final_at(p.price_date)) < INTERVAL '3 days' AS live_captured,
       p.source                                  AS yahoo_source,
       -- Yahoo's close is split/bonus-adjusted, the exchange file is not: before such an event the
       -- two differ by the split factor. Those rows are not comparable and are kept out of the stats.
       (p.close / r.close NOT BETWEEN 0.8 AND 1.25) AS scale_mismatch
FROM daily_prices p
JOIN reference_prices r ON r.ticker = p.ticker AND r.price_date = p.price_date
LEFT JOIN first_print f ON f.ticker = p.ticker AND f.price_date = p.price_date;

-- Headline accuracy per source (and how first prints compare with settled values)
CREATE OR REPLACE VIEW v_source_agreement_summary AS
SELECT source,
       COUNT(*)                                                    AS n_compared,
       COUNT(DISTINCT price_date)                                  AS n_sessions,
       MIN(price_date)                                             AS from_date,
       MAX(price_date)                                             AS to_date,
       ROUND(100.0 * AVG(agrees::int), 3)                          AS agree_pct,
       COUNT(*) FILTER (WHERE NOT agrees)                          AS n_disagree,
       COUNT(*) FILTER (WHERE matches_last_trade_instead)          AS n_last_trade_not_close,
       ROUND(AVG(ABS(diff_bp)), 3)                                 AS mean_abs_diff_bp,
       ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY ABS(diff_bp))::numeric, 3) AS p95_abs_diff_bp,
       ROUND(MAX(ABS(diff_bp)), 3)                                 AS max_abs_diff_bp,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(volume_diff_pct))::numeric, 2) AS median_abs_volume_diff_pct,
       COUNT(*) FILTER (WHERE live_captured)                       AS n_live_first_prints,
       ROUND(100.0 * AVG(first_print_agrees::int) FILTER (WHERE live_captured), 3) AS first_print_agree_pct
       , (SELECT COUNT(*) FROM v_source_agreement x WHERE x.source = a.source AND x.scale_mismatch) AS n_scale_mismatch_excluded
FROM v_source_agreement a
WHERE NOT scale_mismatch
GROUP BY source;

-- Worst disagreements with the SAME exchange, newest first. Yahoo's .NS series is NSE data, so a gap to
-- BSE is a cross-exchange difference, not an error, and is not listed here.
CREATE OR REPLACE VIEW v_source_disagreements AS
SELECT a.price_date, a.ticker, c.company_name, a.source, a.yahoo_close, a.official_close,
       a.official_last_price, a.diff_bp, a.matches_last_trade_instead, a.yahoo_source
FROM v_source_agreement a
JOIN companies c ON c.ticker = a.ticker
WHERE NOT a.agrees AND NOT a.scale_mismatch AND a.source = 'NSE'
ORDER BY a.price_date DESC, ABS(a.diff_bp) DESC;

-- Best available close for each stock-day: official NSE, else official BSE, else Yahoo.
CREATE OR REPLACE VIEW v_consensus_close AS
SELECT p.ticker, p.price_date,
       COALESCE(n.close, b.close, p.close)                           AS best_close,
       CASE WHEN n.close IS NOT NULL THEN 'NSE'
            WHEN b.close IS NOT NULL THEN 'BSE' ELSE 'yahoo' END     AS best_source,
       (n.close IS NOT NULL)::int + (b.close IS NOT NULL)::int + 1   AS n_sources,
       p.close                                                       AS yahoo_close,
       n.close                                                       AS nse_close,
       b.close                                                       AS bse_close,
       ROUND((b.close / NULLIF(n.close, 0) - 1) * 10000, 3)          AS nse_bse_spread_bp
FROM daily_prices p
LEFT JOIN reference_prices n ON n.source = 'NSE' AND n.ticker = p.ticker AND n.price_date = p.price_date
LEFT JOIN reference_prices b ON b.source = 'BSE' AND b.ticker = p.ticker AND b.price_date = p.price_date;

-- Download availability (did the exchanges let us in?)
CREATE OR REPLACE VIEW v_reference_coverage AS
SELECT source, status, COUNT(*) AS n_files, MIN(file_date) AS first_file, MAX(file_date) AS last_file,
       SUM(rows_matched) AS rows_matched
FROM reference_fetch_log
GROUP BY source, status;

GRANT SELECT ON reference_prices, reference_fetch_log, v_source_agreement, v_source_agreement_summary,
                v_source_disagreements, v_consensus_close, v_reference_coverage TO readonly_access;

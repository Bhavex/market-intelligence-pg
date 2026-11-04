CREATE TABLE IF NOT EXISTS reconciliation_results (
    recon_id           BIGSERIAL     PRIMARY KEY,
    run_id             BIGINT,
    fetch_id           BIGINT        REFERENCES fetch_log(fetch_id),
    trade_date         DATE          NOT NULL,
    instrument_id      INTEGER       REFERENCES instruments(instrument_id),
    ticker             VARCHAR(20),
    nse_symbol         VARCHAR(40),
    series             VARCHAR(4),
    isin               VARCHAR(12),
    bhav_format        VARCHAR(12)   CHECK (bhav_format IN ('legacy_cm', 'udiff_cm')),
    bhav_close         NUMERIC(14,4),
    bhav_last          NUMERIC(14,4),
    bhav_prev_close    NUMERIC(14,4),
    bhav_low           NUMERIC(14,4),
    bhav_high          NUMERIC(14,4),
    bhav_volume        BIGINT,
    yahoo_close        NUMERIC(14,4),
    yahoo_volume       BIGINT,
    yahoo_close_basis  VARCHAR(32),
    bhav_close_basis   VARCHAR(32)   NOT NULL DEFAULT 'unadjusted',
    matched_by         VARCHAR(16)   CHECK (matched_by IN ('symbol_series', 'isin')),
    classification     VARCHAR(24)   NOT NULL CHECK (classification IN ('match', 'last_traded_not_close',
                                     'previous_session_close', 'split_or_bonus_basis', 'intraday_value', 'unexplained',
                                     'missing_in_yahoo', 'missing_in_bhavcopy')),
    diff_bp            NUMERIC(12,4),
    detail             TEXT,
    reconciled_at      TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_reconciliation_key ON reconciliation_results (ticker, trade_date, reconciled_at DESC);
CREATE INDEX IF NOT EXISTS idx_reconciliation_class ON reconciliation_results (classification, trade_date);

DROP TRIGGER IF EXISTS reconciliation_results_append_only ON reconciliation_results;
CREATE TRIGGER reconciliation_results_append_only
BEFORE UPDATE OR DELETE ON reconciliation_results
FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE OR REPLACE VIEW v_reconciliation_latest AS
SELECT DISTINCT ON (ticker, trade_date) *
FROM reconciliation_results
ORDER BY ticker, trade_date, reconciled_at DESC, recon_id DESC;

CREATE OR REPLACE VIEW v_reconciliation_summary AS
SELECT bhav_format, classification, COUNT(*) AS n_stock_days, COUNT(DISTINCT ticker) AS n_tickers,
       MIN(trade_date) AS first_date, MAX(trade_date) AS last_date,
       ROUND(AVG(ABS(diff_bp)), 3) AS mean_abs_diff_bp, ROUND(MAX(ABS(diff_bp)), 3) AS max_abs_diff_bp
FROM v_reconciliation_latest
GROUP BY bhav_format, classification;

GRANT SELECT ON reconciliation_results, v_reconciliation_latest, v_reconciliation_summary TO readonly_access;

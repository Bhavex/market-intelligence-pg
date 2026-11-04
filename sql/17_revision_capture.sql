CREATE TABLE IF NOT EXISTS revision_captures (
    capture_id       BIGSERIAL     PRIMARY KEY,
    run_id           BIGINT,
    fetch_id         BIGINT        REFERENCES fetch_log(fetch_id),
    ticker           VARCHAR(20)   NOT NULL REFERENCES companies(ticker) ON UPDATE CASCADE,
    price_date       DATE          NOT NULL,
    sample           VARCHAR(12)   NOT NULL CHECK (sample IN ('recent', 'rotating')),
    kind             VARCHAR(16)   NOT NULL CHECK (kind IN ('value_changed', 'missing_in_feed', 'new_in_feed')),
    field            VARCHAR(12),
    stored_value     NUMERIC(20,4),
    fetched_value    NUMERIC(20,4),
    rel_diff         NUMERIC(14,8),
    stored_loaded_at TIMESTAMPTZ,
    detected_at      TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_revision_captures_key ON revision_captures (ticker, price_date, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_revision_captures_run ON revision_captures (run_id);

DROP TRIGGER IF EXISTS revision_captures_append_only ON revision_captures;
CREATE TRIGGER revision_captures_append_only
BEFORE UPDATE OR DELETE ON revision_captures
FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE IF NOT EXISTS revision_capture_coverage (
    run_id        BIGINT        NOT NULL,
    ticker        VARCHAR(20)   NOT NULL,
    sample        VARCHAR(12)   NOT NULL CHECK (sample IN ('recent', 'rotating')),
    window_start  DATE          NOT NULL,
    window_end    DATE          NOT NULL,
    rows_compared INTEGER       NOT NULL CHECK (rows_compared >= 0),
    rows_differing INTEGER      NOT NULL CHECK (rows_differing >= 0),
    fetch_id      BIGINT        REFERENCES fetch_log(fetch_id),
    checked_at    TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, ticker, sample)
);

CREATE OR REPLACE VIEW v_revision_capture_summary AS
SELECT c.sample, c.kind, c.field,
       COUNT(*) AS n_differences, COUNT(DISTINCT (c.ticker, c.price_date)) AS n_stock_days,
       COUNT(DISTINCT c.ticker) AS n_tickers,
       ROUND(AVG(ABS(c.rel_diff)) * 10000, 3) AS mean_abs_diff_bp,
       ROUND(MAX(ABS(c.rel_diff)) * 10000, 3) AS max_abs_diff_bp,
       MIN(c.price_date) AS first_price_date, MAX(c.price_date) AS last_price_date,
       MIN(c.detected_at) AS first_detected, MAX(c.detected_at) AS last_detected
FROM revision_captures c
GROUP BY c.sample, c.kind, c.field;

CREATE OR REPLACE VIEW v_revision_capture_rate AS
SELECT sample, COUNT(DISTINCT run_id) AS n_runs, SUM(rows_compared) AS rows_compared,
       SUM(rows_differing) AS rows_differing,
       ROUND(SUM(rows_differing)::numeric / NULLIF(SUM(rows_compared), 0) * 100, 4) AS pct_rows_differing
FROM revision_capture_coverage
GROUP BY sample;

GRANT SELECT ON revision_captures, revision_capture_coverage, v_revision_capture_summary, v_revision_capture_rate
    TO readonly_access;

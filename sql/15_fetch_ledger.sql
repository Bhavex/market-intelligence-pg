CREATE TABLE IF NOT EXISTS fetch_log (
    fetch_id                 BIGSERIAL    PRIMARY KEY,
    run_id                   BIGINT,
    job_name                 VARCHAR(40),
    source                   VARCHAR(16)  NOT NULL,
    endpoint                 VARCHAR(40)  NOT NULL,
    symbol                   VARCHAR(40)  NOT NULL,
    request                  JSONB        NOT NULL DEFAULT '{}',
    retrieved_at             TIMESTAMPTZ  NOT NULL,
    response_sha256          CHAR(64)     NOT NULL,
    hash_basis               VARCHAR(32)  NOT NULL CHECK (hash_basis IN ('raw_bytes', 'canonical_frame_csv')),
    response_bytes           BIGINT,
    rows_returned            INTEGER      NOT NULL DEFAULT 0 CHECK (rows_returned >= 0),
    rows_parsed              INTEGER      NOT NULL DEFAULT 0 CHECK (rows_parsed >= 0),
    rows_valid               INTEGER      CHECK (rows_valid >= 0),
    rows_rejected            INTEGER      CHECK (rows_rejected >= 0),
    rows_suspect             INTEGER      CHECK (rows_suspect >= 0),
    rows_held_back           INTEGER      CHECK (rows_held_back >= 0),
    library                  VARCHAR(32),
    library_version          VARCHAR(32),
    settings_id              BIGINT       REFERENCES fetch_settings(settings_id),
    parser_version           VARCHAR(40)  NOT NULL,
    validation_rule_version  VARCHAR(40),
    quality_rule_version     VARCHAR(40),
    status                   VARCHAR(16)  NOT NULL CHECK (status IN ('ok', 'empty', 'error', 'parse_error', 'not_published', 'blocked')),
    error                    TEXT,
    logged_at                TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_fetch_log_symbol_time ON fetch_log (symbol, retrieved_at DESC);
CREATE INDEX IF NOT EXISTS idx_fetch_log_run ON fetch_log (run_id);
CREATE INDEX IF NOT EXISTS idx_fetch_log_hash ON fetch_log (response_sha256);

DROP TRIGGER IF EXISTS fetch_log_append_only ON fetch_log;
CREATE TRIGGER fetch_log_append_only
BEFORE UPDATE OR DELETE ON fetch_log
FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE OR REPLACE VIEW v_fetch_summary AS
SELECT retrieved_at::date AS fetch_day, source, endpoint, status,
       COUNT(*) AS n_fetches, COUNT(DISTINCT symbol) AS n_symbols,
       SUM(rows_returned) AS rows_returned, SUM(rows_parsed) AS rows_parsed,
       SUM(rows_rejected) AS rows_rejected, SUM(rows_suspect) AS rows_suspect,
       COUNT(DISTINCT response_sha256) AS n_distinct_responses
FROM fetch_log
GROUP BY retrieved_at::date, source, endpoint, status;

GRANT SELECT ON fetch_log, v_fetch_summary TO readonly_access;

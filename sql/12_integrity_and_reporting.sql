-- =====================================================================
-- 12_integrity_and_reporting.sql — data-quality rejections, extra integrity, Power BI views
-- Applied by src/setup_db.py with every other file; safe to re-run. See docs/MIGRATION_NOTES.md.
-- =====================================================================

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

GRANT SELECT ON rejected_prices TO readonly_access;

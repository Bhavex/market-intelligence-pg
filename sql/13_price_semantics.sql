CREATE TABLE IF NOT EXISTS fetch_settings (
    settings_id      BIGSERIAL    PRIMARY KEY,
    settings_hash    CHAR(64)     NOT NULL UNIQUE,
    source           VARCHAR(16)  NOT NULL,
    library          VARCHAR(32)  NOT NULL,
    library_version  VARCHAR(32)  NOT NULL,
    interval         VARCHAR(8)   NOT NULL,
    params           JSONB        NOT NULL,
    close_basis      VARCHAR(32)  NOT NULL CHECK (close_basis IN ('unadjusted', 'split_adjusted', 'split_and_dividend_adjusted')),
    adj_close_basis  VARCHAR(32)  NOT NULL CHECK (adj_close_basis IN ('unadjusted', 'split_adjusted', 'split_and_dividend_adjusted')),
    first_used_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_used_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'daily_prices' AND column_name = 'settings_id') THEN
        ALTER TABLE daily_prices ADD COLUMN settings_id BIGINT REFERENCES fetch_settings(settings_id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'pipeline_runs' AND column_name = 'settings_id') THEN
        ALTER TABLE pipeline_runs ADD COLUMN settings_id BIGINT REFERENCES fetch_settings(settings_id);
    END IF;
END $$;

CREATE OR REPLACE VIEW v_price_semantics AS
SELECT p.ticker, p.price_date, p.source, s.settings_id, s.library, s.library_version, s.interval,
       s.close_basis, s.adj_close_basis, s.params
FROM daily_prices p
LEFT JOIN fetch_settings s ON s.settings_id = p.settings_id;

GRANT SELECT ON fetch_settings, v_price_semantics TO readonly_access;

CREATE OR REPLACE FUNCTION forbid_mutation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only: % is not allowed', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TABLE IF NOT EXISTS quality_states (
    state_id      BIGSERIAL     PRIMARY KEY,
    ticker        VARCHAR(20)   NOT NULL REFERENCES companies(ticker) ON UPDATE CASCADE,
    price_date    DATE          NOT NULL,
    state         VARCHAR(12)   NOT NULL CHECK (state IN ('provisional', 'suspect', 'accepted', 'rejected')),
    reason        VARCHAR(40)   NOT NULL,
    rule_version  VARCHAR(16)   NOT NULL,
    close         NUMERIC(14,4),
    adj_close     NUMERIC(14,4),
    evidence      JSONB         NOT NULL DEFAULT '{}',
    decided_at    TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp(),
    run_id        BIGINT
);
CREATE INDEX IF NOT EXISTS idx_quality_states_key_time ON quality_states (ticker, price_date, decided_at DESC, state_id DESC);
CREATE INDEX IF NOT EXISTS idx_quality_states_decided ON quality_states (decided_at);

DROP TRIGGER IF EXISTS quality_states_append_only ON quality_states;
CREATE TRIGGER quality_states_append_only
BEFORE UPDATE OR DELETE ON quality_states
FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE OR REPLACE VIEW v_quality_current AS
SELECT DISTINCT ON (ticker, price_date)
       ticker, price_date, state, reason, rule_version, close, adj_close, evidence, decided_at, run_id, state_id
FROM quality_states
ORDER BY ticker, price_date, decided_at DESC, state_id DESC;

CREATE OR REPLACE FUNCTION quality_as_of(p_at TIMESTAMPTZ)
RETURNS TABLE (ticker VARCHAR, price_date DATE, state VARCHAR, reason VARCHAR, decided_at TIMESTAMPTZ, state_id BIGINT)
LANGUAGE sql STABLE
AS $$
    SELECT DISTINCT ON (q.ticker, q.price_date) q.ticker, q.price_date, q.state, q.reason, q.decided_at, q.state_id
    FROM quality_states q
    WHERE q.decided_at <= p_at
    ORDER BY q.ticker, q.price_date, q.decided_at DESC, q.state_id DESC;
$$;

CREATE OR REPLACE VIEW v_quality_summary AS
SELECT state, reason, rule_version, COUNT(*) AS n_rows, COUNT(DISTINCT ticker) AS n_tickers,
       MIN(price_date) AS first_price_date, MAX(price_date) AS last_price_date
FROM v_quality_current
GROUP BY state, reason, rule_version;

GRANT SELECT ON quality_states, v_quality_current, v_quality_summary TO readonly_access;
GRANT EXECUTE ON FUNCTION quality_as_of(TIMESTAMPTZ) TO readonly_access;

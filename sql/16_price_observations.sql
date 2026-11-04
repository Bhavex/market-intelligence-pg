CREATE OR REPLACE FUNCTION price_value_hash(p_open NUMERIC, p_high NUMERIC, p_low NUMERIC, p_close NUMERIC,
                                            p_adj NUMERIC, p_volume BIGINT)
RETURNS CHAR(64)
LANGUAGE sql IMMUTABLE PARALLEL SAFE
AS $$
    SELECT encode(sha256(convert_to(
        COALESCE(p_open::text, '-') || '|' || COALESCE(p_high::text, '-') || '|' || COALESCE(p_low::text, '-') || '|' ||
        COALESCE(p_close::text, '-') || '|' || COALESCE(p_adj::text, '-') || '|' || COALESCE(p_volume::text, '-'),
        'UTF8')), 'hex');
$$;

CREATE TABLE IF NOT EXISTS price_observations (
    observation_id   BIGSERIAL     PRIMARY KEY,
    fetch_id         BIGINT        REFERENCES fetch_log(fetch_id),
    ticker           VARCHAR(20)   NOT NULL REFERENCES companies(ticker) ON UPDATE CASCADE,
    price_date       DATE          NOT NULL,
    source           VARCHAR(24)   NOT NULL,
    open             NUMERIC(14,4),
    high             NUMERIC(14,4),
    low              NUMERIC(14,4),
    close            NUMERIC(14,4),
    adj_close        NUMERIC(14,4),
    volume           BIGINT,
    close_basis      VARCHAR(32)   NOT NULL,
    adj_close_basis  VARCHAR(32),
    observed_at      TIMESTAMPTZ   NOT NULL,
    value_hash       CHAR(64)      GENERATED ALWAYS AS (price_value_hash(open, high, low, close, adj_close, volume)) STORED,
    recorded_at      TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS idx_price_observations_key ON price_observations (ticker, price_date, source, observation_id DESC);
CREATE INDEX IF NOT EXISTS idx_price_observations_time ON price_observations (observed_at);
CREATE INDEX IF NOT EXISTS idx_price_observations_fetch ON price_observations (fetch_id);

DROP TRIGGER IF EXISTS price_observations_append_only ON price_observations;
CREATE TRIGGER price_observations_append_only
BEFORE UPDATE OR DELETE ON price_observations
FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE TABLE IF NOT EXISTS price_selection (
    selection_id     BIGSERIAL     PRIMARY KEY,
    ticker           VARCHAR(20)   NOT NULL,
    price_date       DATE          NOT NULL,
    action           VARCHAR(8)    NOT NULL CHECK (action IN ('insert', 'update', 'delete')),
    observation_id   BIGINT        REFERENCES price_observations(observation_id),
    source           VARCHAR(24),
    value_hash       CHAR(64),
    selected_at      TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp(),
    run_id           BIGINT
);
CREATE INDEX IF NOT EXISTS idx_price_selection_key_time ON price_selection (ticker, price_date, selected_at DESC, selection_id DESC);

DROP TRIGGER IF EXISTS price_selection_append_only ON price_selection;
CREATE TRIGGER price_selection_append_only
BEFORE UPDATE OR DELETE ON price_selection
FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE OR REPLACE FUNCTION trg_price_selection()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_run  BIGINT := NULLIF(current_setting('app.run_id', true), '')::BIGINT;
    v_hash CHAR(64);
    v_obs  BIGINT;
BEGIN
    IF TG_OP = 'DELETE' THEN
        INSERT INTO price_selection (ticker, price_date, action, source, run_id)
        VALUES (OLD.ticker, OLD.price_date, 'delete', OLD.source, v_run);
        RETURN NULL;
    END IF;
    v_hash := price_value_hash(NEW.open, NEW.high, NEW.low, NEW.close, NEW.adj_close, NEW.volume);
    SELECT o.observation_id INTO v_obs
    FROM price_observations o
    WHERE o.ticker = NEW.ticker AND o.price_date = NEW.price_date AND o.source = NEW.source AND o.value_hash = v_hash
    ORDER BY o.observation_id DESC
    LIMIT 1;
    INSERT INTO price_selection (ticker, price_date, action, observation_id, source, value_hash, run_id)
    VALUES (NEW.ticker, NEW.price_date, lower(TG_OP), v_obs, NEW.source, v_hash, v_run);
    RETURN NULL;
END;
$$;

DROP TRIGGER IF EXISTS select_prices_ins_del ON daily_prices;
CREATE TRIGGER select_prices_ins_del
AFTER INSERT OR DELETE ON daily_prices
FOR EACH ROW EXECUTE FUNCTION trg_price_selection();

DROP TRIGGER IF EXISTS select_prices_upd ON daily_prices;
CREATE TRIGGER select_prices_upd
AFTER UPDATE ON daily_prices
FOR EACH ROW
WHEN ((OLD.open, OLD.high, OLD.low, OLD.close, OLD.adj_close, OLD.volume, OLD.source)
      IS DISTINCT FROM
      (NEW.open, NEW.high, NEW.low, NEW.close, NEW.adj_close, NEW.volume, NEW.source))
EXECUTE FUNCTION trg_price_selection();

CREATE OR REPLACE FUNCTION observations_as_of(p_at TIMESTAMPTZ)
RETURNS TABLE (ticker VARCHAR, price_date DATE, source VARCHAR, observation_id BIGINT, close NUMERIC,
               adj_close NUMERIC, volume BIGINT, observed_at TIMESTAMPTZ, fetch_id BIGINT)
LANGUAGE sql STABLE
AS $$
    SELECT DISTINCT ON (o.ticker, o.price_date, o.source)
           o.ticker, o.price_date, o.source, o.observation_id, o.close, o.adj_close, o.volume, o.observed_at, o.fetch_id
    FROM price_observations o
    WHERE o.observed_at <= p_at
    ORDER BY o.ticker, o.price_date, o.source, o.observed_at DESC, o.observation_id DESC;
$$;

CREATE OR REPLACE FUNCTION selected_prices_as_of(p_at TIMESTAMPTZ)
RETURNS TABLE (ticker VARCHAR, price_date DATE, observation_id BIGINT, source VARCHAR, open NUMERIC, high NUMERIC,
               low NUMERIC, close NUMERIC, adj_close NUMERIC, volume BIGINT, selected_at TIMESTAMPTZ,
               observed_at TIMESTAMPTZ, fetch_id BIGINT)
LANGUAGE sql STABLE
AS $$
    WITH latest AS (
        SELECT DISTINCT ON (s.ticker, s.price_date) s.*
        FROM price_selection s
        WHERE s.selected_at <= p_at
        ORDER BY s.ticker, s.price_date, s.selected_at DESC, s.selection_id DESC
    )
    SELECT l.ticker, l.price_date, l.observation_id, l.source, o.open, o.high, o.low, o.close, o.adj_close, o.volume,
           l.selected_at, o.observed_at, o.fetch_id
    FROM latest l
    LEFT JOIN price_observations o ON o.observation_id = l.observation_id
    WHERE l.action <> 'delete';
$$;

CREATE OR REPLACE VIEW v_observation_revisions AS
SELECT o.ticker, o.price_date, o.source, o.observation_id,
       LAG(o.observation_id) OVER w AS previous_observation_id,
       LAG(o.close) OVER w AS previous_close, o.close,
       LAG(o.adj_close) OVER w AS previous_adj_close, o.adj_close,
       LAG(o.volume) OVER w AS previous_volume, o.volume,
       LAG(o.observed_at) OVER w AS previous_observed_at, o.observed_at, o.fetch_id
FROM price_observations o
WINDOW w AS (PARTITION BY o.ticker, o.price_date, o.source ORDER BY o.observed_at, o.observation_id);

CREATE OR REPLACE VIEW v_selection_provenance AS
SELECT DISTINCT ON (s.ticker, s.price_date)
       s.ticker, s.price_date, s.action, s.observation_id, s.source, s.selected_at, s.run_id,
       o.observed_at, o.fetch_id, f.retrieved_at, f.response_sha256, f.library_version, f.parser_version,
       (s.observation_id IS NOT NULL) AS has_observation
FROM price_selection s
LEFT JOIN price_observations o ON o.observation_id = s.observation_id
LEFT JOIN fetch_log f ON f.fetch_id = o.fetch_id
ORDER BY s.ticker, s.price_date, s.selected_at DESC, s.selection_id DESC;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'quality_states' AND column_name = 'observation_id') THEN
        ALTER TABLE quality_states ADD COLUMN observation_id BIGINT REFERENCES price_observations(observation_id);
    END IF;
END $$;

GRANT SELECT ON price_observations, price_selection, v_observation_revisions, v_selection_provenance TO readonly_access;
GRANT EXECUTE ON FUNCTION observations_as_of(TIMESTAMPTZ), selected_prices_as_of(TIMESTAMPTZ) TO readonly_access;

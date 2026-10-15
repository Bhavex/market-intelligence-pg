-- =====================================================================
-- 08_point_in_time.sql — bitemporal ("point-in-time") price history
--
-- Problem: free market data is revised after it is first published (a missing close filled
-- in later, an intraday price stored as the close, back-adjustment after a dividend ...).
-- daily_prices only keeps the latest value, so the database could not answer
-- "what did we believe about INFY's 13 Oct close at 10 AM on 13 Oct?".
--
-- Two time axes (SQL:2011 calls this a bitemporal table):
--   * valid time       = price_date              (the trading session the fact describes)
--   * transaction time = [sys_from, sys_to)      (when the database held that version)
-- Every insert / value change / delete on daily_prices is captured by a trigger, with the
-- pipeline run that caused it (provenance). Nothing is ever overwritten here.
--
-- Safe to re-run (setup_db.py runs every file before each load).
-- =====================================================================

-- ---------- Row-level provenance on the live table ----------
-- Where the current value came from: Yahoo's daily candle, or a day rebuilt from hourly bars.
-- (checked first, so the ALTER and its table lock only happen once, not on every setup run)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'daily_prices' AND column_name = 'source') THEN
        ALTER TABLE daily_prices ADD COLUMN source VARCHAR(24) NOT NULL DEFAULT 'yahoo_daily';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'daily_prices_source_chk') THEN
        ALTER TABLE daily_prices ADD CONSTRAINT daily_prices_source_chk
            CHECK (source IN ('yahoo_daily', 'yahoo_hourly'));
    END IF;
END $$;

-- ---------- The version table ----------
CREATE TABLE IF NOT EXISTS price_versions (
    version_id   BIGSERIAL     PRIMARY KEY,
    ticker       VARCHAR(20)   NOT NULL,
    price_date   DATE          NOT NULL,                 -- valid time
    open         NUMERIC(14,4),
    high         NUMERIC(14,4),
    low          NUMERIC(14,4),
    close        NUMERIC(14,4) NOT NULL,
    adj_close    NUMERIC(14,4),
    volume       BIGINT,
    source       VARCHAR(24)   NOT NULL DEFAULT 'yahoo_daily',
    sys_from     TIMESTAMPTZ   NOT NULL,                 -- transaction time: version became current
    sys_to       TIMESTAMPTZ,                            -- NULL = still current
    run_id       BIGINT,                                 -- pipeline_runs.run_id that wrote it (NULL = manual / seed)
    origin       VARCHAR(12)   NOT NULL DEFAULT 'trigger'
                 CHECK (origin IN ('trigger', 'seed', 'audit_log')),
    CHECK (sys_to IS NULL OR sys_to > sys_from)
);

-- At most one current version per (ticker, day)
CREATE UNIQUE INDEX IF NOT EXISTS ux_price_versions_current
    ON price_versions (ticker, price_date) WHERE sys_to IS NULL;
CREATE INDEX IF NOT EXISTS idx_price_versions_key_time
    ON price_versions (ticker, price_date, sys_from);
CREATE INDEX IF NOT EXISTS idx_price_versions_sys_from
    ON price_versions (sys_from);

-- Temporal integrity: two versions of the same (ticker, day) may never overlap in transaction
-- time. Needs the btree_gist extension; if it cannot be installed the partial unique index
-- above still guarantees a single current version, so setup never fails because of it.
DO $$
BEGIN
    BEGIN
        CREATE EXTENSION IF NOT EXISTS btree_gist;
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'btree_gist not available (%), temporal exclusion constraint skipped', SQLERRM;
    END;
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'btree_gist')
       AND NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'price_versions_no_overlap') THEN
        ALTER TABLE price_versions ADD CONSTRAINT price_versions_no_overlap
            EXCLUDE USING gist (ticker WITH =, price_date WITH =,
                                tstzrange(sys_from, sys_to, '[)') WITH &&);
    END IF;
END $$;

-- ---------- Versioning trigger ----------
-- Pipeline scripts call  SELECT set_config('app.run_id', '<run_id>', false)  (see src/db.py),
-- so every version records which run produced it.
CREATE OR REPLACE FUNCTION trg_price_versioning()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_now TIMESTAMPTZ := now();          -- transaction start: all changes of one load share it
    v_run BIGINT      := NULLIF(current_setting('app.run_id', true), '')::BIGINT;
    v_cur TIMESTAMPTZ;                   -- sys_from of the current version
    v_ts  TIMESTAMPTZ;                   -- time this change takes effect
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        SELECT sys_from INTO v_cur
        FROM price_versions
        WHERE ticker = OLD.ticker AND price_date = OLD.price_date AND sys_to IS NULL
        FOR UPDATE;

        IF v_cur = v_now THEN
            -- the current version was written by THIS transaction and never seen by anyone else
            IF TG_OP = 'UPDATE' AND OLD.ticker = NEW.ticker AND OLD.price_date = NEW.price_date THEN
                UPDATE price_versions
                SET open = NEW.open, high = NEW.high, low = NEW.low, close = NEW.close,
                    adj_close = NEW.adj_close, volume = NEW.volume, source = NEW.source,
                    run_id = COALESCE(v_run, run_id)
                WHERE ticker = OLD.ticker AND price_date = OLD.price_date AND sys_to IS NULL;
                RETURN NULL;
            END IF;
            DELETE FROM price_versions
            WHERE ticker = OLD.ticker AND price_date = OLD.price_date AND sys_to IS NULL;
            v_ts := v_now;
        ELSE
            -- A transaction that started earlier but commits later than the one that wrote the
            -- current version (overlapping loads): never close a version before it began.
            v_ts := GREATEST(v_now, COALESCE(v_cur + INTERVAL '1 microsecond', v_now));
            UPDATE price_versions SET sys_to = v_ts
            WHERE ticker = OLD.ticker AND price_date = OLD.price_date AND sys_to IS NULL;
        END IF;
    END IF;

    IF TG_OP IN ('INSERT', 'UPDATE') THEN
        IF v_ts IS NULL THEN              -- plain INSERT: start after any earlier (deleted) version
            SELECT GREATEST(v_now, COALESCE(MAX(sys_to), v_now)) INTO v_ts
            FROM price_versions WHERE ticker = NEW.ticker AND price_date = NEW.price_date;
        END IF;
        INSERT INTO price_versions (ticker, price_date, open, high, low, close, adj_close,
                                    volume, source, sys_from, run_id, origin)
        VALUES (NEW.ticker, NEW.price_date, NEW.open, NEW.high, NEW.low, NEW.close, NEW.adj_close,
                NEW.volume, NEW.source, v_ts, v_run, 'trigger');
    END IF;
    RETURN NULL;                          -- AFTER trigger: return value ignored
END;
$$;

DROP TRIGGER IF EXISTS version_prices_ins_del ON daily_prices;
CREATE TRIGGER version_prices_ins_del
AFTER INSERT OR DELETE ON daily_prices
FOR EACH ROW EXECUTE FUNCTION trg_price_versioning();

-- Only real changes create a new version (a no-change re-load does not).
DROP TRIGGER IF EXISTS version_prices_upd ON daily_prices;
CREATE TRIGGER version_prices_upd
AFTER UPDATE ON daily_prices
FOR EACH ROW
WHEN ((OLD.ticker, OLD.price_date, OLD.open, OLD.high, OLD.low, OLD.close, OLD.adj_close, OLD.volume, OLD.source)
      IS DISTINCT FROM
      (NEW.ticker, NEW.price_date, NEW.open, NEW.high, NEW.low, NEW.close, NEW.adj_close, NEW.volume, NEW.source))
EXECUTE FUNCTION trg_price_versioning();

-- ---------- One-time seed (runs only while price_versions is empty) ----------
-- 1. Past versions recovered from the audit log: every logged correction/deletion stores the
--    old row, whose loaded_at is when that version became current and changed_at when it ended.
-- 2. The current row of daily_prices is the open-ended latest version.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM price_versions) THEN
        RETURN;
    END IF;
    BEGIN
        INSERT INTO price_versions (ticker, price_date, open, high, low, close, adj_close, volume,
                                    source, sys_from, sys_to, origin)
        SELECT ticker, price_date, open, high, low, close, adj_close, volume,
               source, sys_from, sys_to, 'audit_log'
        FROM (
            SELECT a.old_data->>'ticker'                       AS ticker,
                   (a.old_data->>'price_date')::date           AS price_date,
                   (a.old_data->>'open')::numeric              AS open,
                   (a.old_data->>'high')::numeric              AS high,
                   (a.old_data->>'low')::numeric               AS low,
                   (a.old_data->>'close')::numeric             AS close,
                   (a.old_data->>'adj_close')::numeric         AS adj_close,
                   (a.old_data->>'volume')::bigint             AS volume,
                   COALESCE(a.old_data->>'source', 'yahoo_daily') AS source,
                   (a.old_data->>'loaded_at')::timestamptz     AS sys_from,
                   a.changed_at                                AS sys_to,
                   -- keep one version per key and start time (defensive against odd log rows)
                   ROW_NUMBER() OVER (PARTITION BY a.old_data->>'ticker', a.old_data->>'price_date',
                                                   a.old_data->>'loaded_at'
                                      ORDER BY a.audit_id) AS rn
            FROM audit_log a
            WHERE a.table_name = 'daily_prices'
              AND a.operation IN ('UPDATE', 'DELETE')
              AND a.old_data ? 'loaded_at'
        ) x
        WHERE rn = 1 AND sys_to > sys_from AND close IS NOT NULL;

        INSERT INTO price_versions (ticker, price_date, open, high, low, close, adj_close, volume,
                                    source, sys_from, sys_to, origin)
        SELECT ticker, price_date, open, high, low, close, adj_close, volume,
               source, loaded_at, NULL, 'seed'
        FROM daily_prices;
    EXCEPTION WHEN OTHERS THEN
        -- e.g. overlapping history in an unusual audit log: fall back to current versions only
        RAISE NOTICE 'audit-log history not usable (%); seeding current versions only', SQLERRM;
        DELETE FROM price_versions;
        INSERT INTO price_versions (ticker, price_date, open, high, low, close, adj_close, volume,
                                    source, sys_from, sys_to, origin)
        SELECT ticker, price_date, open, high, low, close, adj_close, volume,
               source, loaded_at, NULL, 'seed'
        FROM daily_prices;
    END;
END $$;

-- ---------- Time travel ----------
-- The whole price table exactly as the database knew it at p_at:
--   SELECT * FROM prices_as_of('2026-10-13 10:00+05:30') WHERE ticker = 'INFY.NS';
CREATE OR REPLACE FUNCTION prices_as_of(p_at TIMESTAMPTZ)
RETURNS TABLE (ticker VARCHAR, price_date DATE, open NUMERIC, high NUMERIC, low NUMERIC,
               close NUMERIC, adj_close NUMERIC, volume BIGINT, source VARCHAR,
               version_id BIGINT, known_since TIMESTAMPTZ)
LANGUAGE sql STABLE
AS $$
    SELECT v.ticker, v.price_date, v.open, v.high, v.low, v.close, v.adj_close, v.volume,
           v.source, v.version_id, v.sys_from
    FROM price_versions v
    WHERE v.sys_from <= p_at
      AND (v.sys_to IS NULL OR v.sys_to > p_at);
$$;

-- Full version history of one stock-day (bitemporal lookup)
--   SELECT * FROM price_history('INFY.NS', '2026-10-13');
CREATE OR REPLACE FUNCTION price_history(p_ticker VARCHAR, p_date DATE)
RETURNS TABLE (version_no BIGINT, close NUMERIC, adj_close NUMERIC, volume BIGINT, source VARCHAR,
               known_from TIMESTAMPTZ, known_to TIMESTAMPTZ, run_id BIGINT, origin VARCHAR)
LANGUAGE sql STABLE
AS $$
    SELECT ROW_NUMBER() OVER (ORDER BY sys_from), close, adj_close, volume, source,
           sys_from, sys_to, run_id, origin
    FROM price_versions
    WHERE ticker = upper(p_ticker) AND price_date = p_date
    ORDER BY sys_from;
$$;

-- When a session's data is final: NSE closes at 15:30 IST; 16:30 IST includes the same
-- settling buffer the pipeline uses (prices.FINAL_AFTER, expected_latest_trading_day()).
CREATE OR REPLACE FUNCTION session_final_at(p_day DATE)
RETURNS TIMESTAMPTZ
LANGUAGE sql IMMUTABLE
AS $$
    SELECT (p_day + TIME '16:30') AT TIME ZONE 'Asia/Kolkata';
$$;

-- ---------- Revision taxonomy ----------
-- Classifies the change from one version to the next. Order matters: the first rule that
-- matches wins. Thresholds: prices are stored to 4 decimals, so < 0.00005 means "unchanged".
CREATE OR REPLACE FUNCTION classify_revision(
    p_prev_close NUMERIC, p_new_close NUMERIC,
    p_prev_adj NUMERIC,   p_new_adj NUMERIC,
    p_prev_open NUMERIC,  p_new_open NUMERIC,
    p_prev_high NUMERIC,  p_new_high NUMERIC,
    p_prev_low NUMERIC,   p_new_low NUMERIC,
    p_prev_vol BIGINT,    p_new_vol BIGINT,
    p_prev_source VARCHAR, p_prev_known_from TIMESTAMPTZ, p_price_date DATE)
RETURNS VARCHAR
LANGUAGE plpgsql IMMUTABLE
AS $$
DECLARE
    c_same  BOOLEAN := ABS(p_new_close - p_prev_close) < 0.00005;
    a_same  BOOLEAN := p_prev_adj IS NOT DISTINCT FROM p_new_adj
                       OR ABS(COALESCE(p_new_adj, 0) - COALESCE(p_prev_adj, 0)) < 0.00005;
    ohl_same BOOLEAN := (p_prev_open, p_prev_high, p_prev_low) IS NOT DISTINCT FROM (p_new_open, p_new_high, p_new_low);
    r_close NUMERIC  := p_new_close / NULLIF(p_prev_close, 0);
    r_adj   NUMERIC  := p_new_adj / NULLIF(p_prev_adj, 0);
BEGIN
    IF p_prev_known_from < session_final_at(p_price_date) THEN
        RETURN 'intraday_as_close';            -- first version was stored before the session was final
    ELSIF p_prev_source = 'yahoo_hourly' THEN
        RETURN 'hourly_fill_replaced';         -- gap filled from hourly bars, later replaced by the daily candle
    ELSIF c_same AND NOT a_same THEN
        RETURN 'dividend_adjustment';          -- raw close kept, adjusted history rescaled
    ELSIF NOT c_same AND r_close IS NOT NULL AND (r_close < 0.8 OR r_close > 1.25)
          AND r_adj IS NOT NULL AND ABS(r_adj / r_close - 1) < 0.01 THEN
        RETURN 'split_or_bonus_adjustment';    -- whole price level rescaled by a corporate action
    ELSIF c_same AND a_same AND ohl_same AND p_prev_vol IS DISTINCT FROM p_new_vol THEN
        RETURN 'volume_revision';
    ELSIF c_same AND a_same THEN
        RETURN 'ohl_revision';                 -- open/high/low changed, close untouched
    ELSE
        RETURN 'price_restatement';            -- close itself changed for another reason
    END IF;
END;
$$;

-- Every revision ever recorded: one row per change from a version to its successor
-- (or to nothing, for a deletion).
CREATE OR REPLACE VIEW v_price_revisions AS
WITH chain AS (
    SELECT v.*,
           LEAD(v.version_id) OVER k AS next_version_id,
           LEAD(v.close)      OVER k AS next_close,
           LEAD(v.adj_close)  OVER k AS next_adj_close,
           LEAD(v.open)       OVER k AS next_open,
           LEAD(v.high)       OVER k AS next_high,
           LEAD(v.low)        OVER k AS next_low,
           LEAD(v.volume)     OVER k AS next_volume,
           LEAD(v.source)     OVER k AS next_source,
           LEAD(v.run_id)     OVER k AS next_run_id,
           LEAD(v.sys_from)   OVER k AS next_sys_from
    FROM price_versions v
    WINDOW k AS (PARTITION BY v.ticker, v.price_date ORDER BY v.sys_from)
)
SELECT c.ticker, c.price_date,
       c.version_id                     AS old_version_id,
       c.next_version_id                AS new_version_id,
       c.sys_to                         AS revised_at,
       c.next_run_id                    AS revised_by_run,
       c.sys_from                       AS old_known_from,
       c.close                          AS old_close,
       c.next_close                     AS new_close,
       c.adj_close                      AS old_adj_close,
       c.next_adj_close                 AS new_adj_close,
       c.volume                         AS old_volume,
       c.next_volume                    AS new_volume,
       c.source                         AS old_source,
       c.next_source                    AS new_source,
       ROUND((c.next_close / NULLIF(c.close, 0) - 1) * 10000, 2)         AS close_change_bp,
       ROUND((c.next_adj_close / NULLIF(c.adj_close, 0) - 1) * 10000, 2) AS adj_change_bp,
       CASE WHEN c.next_version_id IS NULL OR c.next_sys_from > c.sys_to THEN 'deleted'   -- removed (maybe re-added later)
            ELSE classify_revision(c.close, c.next_close, c.adj_close, c.next_adj_close,
                                   c.open, c.next_open, c.high, c.next_high, c.low, c.next_low,
                                   c.volume, c.next_volume, c.source, c.sys_from, c.price_date)
       END                              AS revision_kind,
       c.sys_to - session_final_at(c.price_date) AS revised_after_session
FROM chain c
WHERE c.sys_to IS NOT NULL;

-- One row per stock-day that was ever revised: first print vs final value and how long the
-- data took to settle.
CREATE OR REPLACE VIEW v_settlement AS
SELECT v.ticker, v.price_date,
       COUNT(*)                                            AS n_versions,
       MIN(v.sys_from)                                     AS first_seen,
       MAX(v.sys_from)                                     AS last_changed,
       MAX(v.sys_from) - session_final_at(v.price_date)    AS settled_after_session,
       (ARRAY_AGG(v.close ORDER BY v.sys_from))[1]         AS first_print_close,
       (ARRAY_AGG(v.close ORDER BY v.sys_from DESC))[1]    AS final_close,
       ROUND(((ARRAY_AGG(v.close ORDER BY v.sys_from DESC))[1]
              / NULLIF((ARRAY_AGG(v.close ORDER BY v.sys_from))[1], 0) - 1) * 10000, 2) AS first_to_final_bp
FROM price_versions v
GROUP BY v.ticker, v.price_date
HAVING COUNT(*) > 1;

-- Headline numbers per revision kind
CREATE OR REPLACE VIEW v_revision_summary AS
SELECT revision_kind,
       COUNT(*)                                         AS n_revisions,
       COUNT(DISTINCT ticker)                           AS n_tickers,
       COUNT(DISTINCT (ticker, price_date))             AS n_stock_days,
       MIN(price_date)                                  AS first_affected_day,
       MAX(price_date)                                  AS last_affected_day,
       ROUND(AVG(ABS(close_change_bp)), 2)              AS avg_abs_close_change_bp,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(close_change_bp))::numeric, 2) AS median_abs_close_change_bp,
       ROUND(MAX(ABS(close_change_bp)), 2)              AS max_abs_close_change_bp,
       ROUND(AVG(ABS(adj_change_bp)), 2)                AS avg_abs_adj_change_bp
FROM v_price_revisions
GROUP BY revision_kind;

GRANT SELECT ON price_versions, v_price_revisions, v_settlement, v_revision_summary TO readonly_access;
GRANT EXECUTE ON FUNCTION prices_as_of(TIMESTAMPTZ), price_history(VARCHAR, DATE) TO readonly_access;

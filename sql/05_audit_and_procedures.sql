-- =====================================================================
-- 05_audit_and_procedures.sql — triggers, audit log, stored procedure
-- Safe to re-run.
-- =====================================================================

-- ---------- Audit log ----------
-- Every change to reference data (companies), fundamentals, and every
-- correction/deletion of a price row is recorded here by triggers.
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id     BIGSERIAL   PRIMARY KEY,
    table_name   VARCHAR(40) NOT NULL,
    operation    VARCHAR(10) NOT NULL CHECK (operation IN ('INSERT','UPDATE','DELETE')),
    row_key      TEXT        NOT NULL,          -- e.g. 'INFY.NS' or 'INFY.NS|2026-10-09'
    old_data     JSONB,
    new_data     JSONB,
    changed_by   TEXT        NOT NULL DEFAULT current_user,
    changed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_log_table_time ON audit_log (table_name, changed_at DESC);

-- One generic trigger function for all audited tables
CREATE OR REPLACE FUNCTION trg_audit()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_row  RECORD;
    v_key  TEXT;
BEGIN
    v_row := CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;

    v_key := CASE TG_TABLE_NAME
                 WHEN 'daily_prices' THEN to_jsonb(v_row)->>'ticker' || '|' || (to_jsonb(v_row)->>'price_date')
                 WHEN 'fundamentals' THEN to_jsonb(v_row)->>'ticker' || '|' || (to_jsonb(v_row)->>'snapshot_date')
                 ELSE to_jsonb(v_row)->>'ticker'
             END;

    INSERT INTO audit_log (table_name, operation, row_key, old_data, new_data)
    VALUES (TG_TABLE_NAME, TG_OP, v_key,
            CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN to_jsonb(OLD) END,
            CASE WHEN TG_OP IN ('INSERT','UPDATE') THEN to_jsonb(NEW) END);

    RETURN v_row;
END;
$$;

-- companies + fundamentals: log inserts and deletes, and updates that
-- actually change something (the daily re-seed doesn't spam the log)
DROP TRIGGER IF EXISTS audit_companies ON companies;
CREATE TRIGGER audit_companies
AFTER INSERT OR DELETE ON companies
FOR EACH ROW EXECUTE FUNCTION trg_audit();

DROP TRIGGER IF EXISTS audit_companies_upd ON companies;
CREATE TRIGGER audit_companies_upd
AFTER UPDATE ON companies
FOR EACH ROW WHEN (OLD IS DISTINCT FROM NEW)
EXECUTE FUNCTION trg_audit();

DROP TRIGGER IF EXISTS audit_fundamentals ON fundamentals;
CREATE TRIGGER audit_fundamentals
AFTER INSERT OR DELETE ON fundamentals
FOR EACH ROW EXECUTE FUNCTION trg_audit();

DROP TRIGGER IF EXISTS audit_fundamentals_upd ON fundamentals;
CREATE TRIGGER audit_fundamentals_upd
AFTER UPDATE ON fundamentals
FOR EACH ROW WHEN (OLD IS DISTINCT FROM NEW)
EXECUTE FUNCTION trg_audit();

-- daily_prices: only log real corrections (values changed) and deletions,
-- not the thousands of normal inserts or no-change re-loads. A change of adj_close ALONE is Yahoo
-- rescaling history after a dividend (thousands of rows each time); it is kept, compactly, in
-- price_versions (sql/08_point_in_time.sql), so it is not duplicated here as JSON.
DROP TRIGGER IF EXISTS audit_price_corrections ON daily_prices;
CREATE TRIGGER audit_price_corrections
AFTER UPDATE ON daily_prices
FOR EACH ROW
WHEN ((OLD.open, OLD.high, OLD.low, OLD.close, OLD.volume)
      IS DISTINCT FROM
      (NEW.open, NEW.high, NEW.low, NEW.close, NEW.volume))
EXECUTE FUNCTION trg_audit();

DROP TRIGGER IF EXISTS audit_price_deletes ON daily_prices;
CREATE TRIGGER audit_price_deletes
AFTER DELETE ON daily_prices
FOR EACH ROW EXECUTE FUNCTION trg_audit();

-- ---------- Validation trigger ----------
-- Normalises tickers to upper case and blocks future-dated prices.
CREATE OR REPLACE FUNCTION trg_validate_price()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.ticker := upper(NEW.ticker);
    IF NEW.price_date > CURRENT_DATE + 1 THEN
        RAISE EXCEPTION 'Price date % for % is in the future', NEW.price_date, NEW.ticker;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS validate_price ON daily_prices;
CREATE TRIGGER validate_price
BEFORE INSERT OR UPDATE ON daily_prices
FOR EACH ROW EXECUTE FUNCTION trg_validate_price();

-- ---------- Stored procedure with a transaction ----------
-- Replace a broken ticker with a new one in ONE atomic step
-- (real case: TATAMOTORS.NS was replaced after the Tata Motors demerger).
-- If any step fails, PostgreSQL rolls the whole procedure back.
--   CALL replace_company('OLD.NS', 'NEW.NS', 'New Company Ltd');
CREATE OR REPLACE PROCEDURE replace_company(p_old_ticker  VARCHAR,
                                            p_new_ticker  VARCHAR,
                                            p_new_name    VARCHAR)
LANGUAGE plpgsql
AS $$
DECLARE
    v_sector SMALLINT;
    v_city   SMALLINT;
BEGIN
    SELECT sector_id, hq_city_id INTO v_sector, v_city
    FROM companies WHERE ticker = p_old_ticker AND is_active
    FOR UPDATE;                                   -- lock the row

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Active company % not found', p_old_ticker;
    END IF;

    UPDATE companies SET is_active = FALSE WHERE ticker = p_old_ticker;

    INSERT INTO companies (ticker, company_name, sector_id, hq_city_id)
    VALUES (upper(p_new_ticker), p_new_name, v_sector, v_city);
    -- The next daily_update run back-fills the new ticker's history automatically.
END;
$$;

-- ---------- Read-only access (for teammates / Power BI) ----------
-- A group role with SELECT-only rights. Login users are added to it with:
--   GRANT readonly_access TO <user>;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'readonly_access') THEN
        CREATE ROLE readonly_access NOLOGIN;
    END IF;
END
$$;

GRANT USAGE  ON SCHEMA public TO readonly_access;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_access;
-- tables/views created later by this owner are readable too
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO readonly_access;

-- ---------- Audit view ----------
CREATE OR REPLACE VIEW v_recent_changes AS
SELECT changed_at, table_name, operation, row_key, changed_by,
       old_data->>'close' AS old_close,
       new_data->>'close' AS new_close
FROM audit_log
ORDER BY changed_at DESC
LIMIT 200;

GRANT SELECT ON v_recent_changes TO readonly_access;

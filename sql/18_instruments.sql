CREATE TABLE IF NOT EXISTS instruments (
    instrument_id   SERIAL        PRIMARY KEY,
    isin            VARCHAR(12)   UNIQUE,
    name            VARCHAR(120)  NOT NULL,
    exchange        VARCHAR(8)    NOT NULL DEFAULT 'NSE' CHECK (exchange IN ('NSE', 'BSE')),
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS instrument_symbols (
    instrument_id   INTEGER       NOT NULL REFERENCES instruments(instrument_id),
    vendor          VARCHAR(16)   NOT NULL CHECK (vendor IN ('yahoo', 'nse', 'bse')),
    symbol          VARCHAR(40)   NOT NULL,
    series          VARCHAR(4),
    valid_from      DATE          NOT NULL,
    valid_to        DATE,
    evidence        VARCHAR(24)   NOT NULL DEFAULT 'seed',
    PRIMARY KEY (vendor, symbol, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

CREATE TABLE IF NOT EXISTS universe_membership (
    universe        VARCHAR(24)   NOT NULL,
    instrument_id   INTEGER       NOT NULL REFERENCES instruments(instrument_id),
    sector_id       SMALLINT      REFERENCES sectors(sector_id),
    valid_from      DATE          NOT NULL,
    valid_to        DATE,
    reason          TEXT          NOT NULL,
    evidence        VARCHAR(24)   NOT NULL DEFAULT 'seed',
    PRIMARY KEY (universe, instrument_id, valid_from),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'btree_gist') THEN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'instrument_symbols_no_overlap') THEN
            ALTER TABLE instrument_symbols ADD CONSTRAINT instrument_symbols_no_overlap
                EXCLUDE USING gist (vendor WITH =, symbol WITH =, daterange(valid_from, valid_to, '[)') WITH &&);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'universe_membership_no_overlap') THEN
            ALTER TABLE universe_membership ADD CONSTRAINT universe_membership_no_overlap
                EXCLUDE USING gist (universe WITH =, instrument_id WITH =, daterange(valid_from, valid_to, '[)') WITH &&);
        END IF;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema = 'public' AND table_name = 'companies' AND column_name = 'instrument_id') THEN
        ALTER TABLE companies ADD COLUMN instrument_id INTEGER REFERENCES instruments(instrument_id);
    END IF;
END $$;

INSERT INTO companies (ticker, company_name, sector_id, hq_city_id, is_active, in_dashboard)
SELECT 'TATAMOTORS.NS', 'Tata Motors', s.sector_id, c.city_id, FALSE, TRUE
FROM sectors s, cities c
WHERE s.sector_name = 'Auto' AND c.city_name = 'Mumbai'
ON CONFLICT (ticker) DO NOTHING;

DO $$
DECLARE
    r RECORD;
    v_id INTEGER;
BEGIN
    FOR r IN SELECT ticker, company_name, isin FROM companies WHERE instrument_id IS NULL ORDER BY ticker LOOP
        IF r.isin IS NOT NULL THEN
            SELECT instrument_id INTO v_id FROM instruments WHERE isin = r.isin;
        ELSE
            v_id := NULL;
        END IF;
        IF v_id IS NULL THEN
            INSERT INTO instruments (isin, name) VALUES (r.isin, r.company_name) RETURNING instrument_id INTO v_id;
        END IF;
        UPDATE companies SET instrument_id = v_id WHERE ticker = r.ticker;
    END LOOP;
END $$;

CREATE OR REPLACE VIEW v_tata_hero_switch AS
WITH evidence AS (
    SELECT
        (SELECT MIN(a.changed_at)::date FROM audit_log a
         WHERE a.table_name = 'companies' AND a.row_key = 'TATAMOTORS.NS' AND a.operation = 'UPDATE'
           AND a.old_data->>'is_active' = 'true' AND a.new_data->>'is_active' = 'false') AS from_audit,
        (SELECT MAX(p.price_date) + 1 FROM daily_prices p WHERE p.ticker = 'TATAMOTORS.NS') AS from_last_price
)
SELECT COALESCE(from_audit, from_last_price, DATE '2025-10-14') AS switch_date,
       CASE WHEN from_audit IS NOT NULL THEN 'audit_log'
            WHEN from_last_price IS NOT NULL THEN 'last_price_date'
            ELSE 'fallback_estimate' END AS evidence
FROM evidence;

INSERT INTO instrument_symbols (instrument_id, vendor, symbol, series, valid_from, valid_to, evidence)
SELECT c.instrument_id, 'yahoo', c.ticker, 'EQ', DATE '1990-01-01',
       CASE WHEN c.ticker = 'TATAMOTORS.NS' THEN (SELECT switch_date FROM v_tata_hero_switch) END,
       CASE WHEN c.ticker = 'TATAMOTORS.NS' THEN (SELECT evidence FROM v_tata_hero_switch) ELSE 'seed' END
FROM companies c
WHERE c.instrument_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM instrument_symbols s WHERE s.vendor = 'yahoo' AND s.symbol = c.ticker);

INSERT INTO instrument_symbols (instrument_id, vendor, symbol, series, valid_from, evidence)
SELECT c.instrument_id, 'nse', replace(c.ticker, '.NS', ''), 'EQ', DATE '1990-01-01', 'seed'
FROM companies c
WHERE c.instrument_id IS NOT NULL AND c.ticker LIKE '%.NS' AND c.ticker <> 'TATAMOTORS.NS'
  AND NOT EXISTS (SELECT 1 FROM instrument_symbols s WHERE s.vendor = 'nse' AND s.symbol = replace(c.ticker, '.NS', ''));

INSERT INTO universe_membership (universe, instrument_id, sector_id, valid_from, valid_to, reason, evidence)
SELECT 'dashboard_25', c.instrument_id, c.sector_id, DATE '2015-01-01',
       CASE WHEN c.ticker = 'TATAMOTORS.NS' THEN (SELECT switch_date FROM v_tata_hero_switch) END,
       CASE WHEN c.ticker = 'TATAMOTORS.NS' THEN 'original Auto member; removed after the Tata Motors demerger broke the symbol'
            ELSE 'original dashboard member' END,
       CASE WHEN c.ticker = 'TATAMOTORS.NS' THEN (SELECT evidence FROM v_tata_hero_switch) ELSE 'seed' END
FROM companies c
WHERE c.in_dashboard AND c.instrument_id IS NOT NULL AND c.ticker <> 'HEROMOTOCO.NS'
  AND (c.is_active OR c.ticker = 'TATAMOTORS.NS')
  AND NOT EXISTS (SELECT 1 FROM universe_membership m WHERE m.universe = 'dashboard_25' AND m.instrument_id = c.instrument_id);

INSERT INTO universe_membership (universe, instrument_id, sector_id, valid_from, reason, evidence)
SELECT 'dashboard_25', c.instrument_id, c.sector_id, (SELECT switch_date FROM v_tata_hero_switch),
       'replaced Tata Motors in the Auto sector', (SELECT evidence FROM v_tata_hero_switch)
FROM companies c
WHERE c.ticker = 'HEROMOTOCO.NS' AND c.instrument_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM universe_membership m WHERE m.universe = 'dashboard_25' AND m.instrument_id = c.instrument_id);

INSERT INTO universe_membership (universe, instrument_id, sector_id, valid_from, reason, evidence)
SELECT 'research_' || c.cap_group, c.instrument_id, c.sector_id, c.added_at::date,
       'added to the data-trust study universe', 'companies.added_at'
FROM companies c
WHERE NOT c.in_dashboard AND c.is_active AND c.instrument_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM universe_membership m WHERE m.universe = 'research_' || c.cap_group AND m.instrument_id = c.instrument_id);

CREATE OR REPLACE FUNCTION universe_members(p_universe VARCHAR, p_on DATE)
RETURNS TABLE (instrument_id INTEGER, ticker VARCHAR, sector_id SMALLINT, valid_from DATE, valid_to DATE)
LANGUAGE sql STABLE
AS $$
    SELECT m.instrument_id, s.symbol, m.sector_id, m.valid_from, m.valid_to
    FROM universe_membership m
    JOIN instrument_symbols s ON s.instrument_id = m.instrument_id AND s.vendor = 'yahoo'
     AND s.valid_from <= p_on AND (s.valid_to IS NULL OR s.valid_to > p_on)
    WHERE m.universe = p_universe AND m.valid_from <= p_on AND (m.valid_to IS NULL OR m.valid_to > p_on);
$$;

CREATE OR REPLACE VIEW v_universe_membership AS
SELECT m.universe, m.instrument_id, i.name, i.isin, s.symbol AS ticker, sec.sector_name, m.valid_from, m.valid_to,
       m.reason, m.evidence
FROM universe_membership m
JOIN instruments i ON i.instrument_id = m.instrument_id
LEFT JOIN LATERAL (
    SELECT x.symbol FROM instrument_symbols x
    WHERE x.instrument_id = m.instrument_id AND x.vendor = 'yahoo'
    ORDER BY x.valid_from DESC LIMIT 1
) s ON TRUE
LEFT JOIN sectors sec ON sec.sector_id = m.sector_id;

GRANT SELECT ON instruments, instrument_symbols, universe_membership, v_universe_membership, v_tata_hero_switch
    TO readonly_access;
GRANT EXECUTE ON FUNCTION universe_members(VARCHAR, DATE) TO readonly_access;

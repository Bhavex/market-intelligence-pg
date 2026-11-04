CREATE TABLE IF NOT EXISTS calendar_coverage (
    year        SMALLINT     PRIMARY KEY,
    source      TEXT         NOT NULL,
    recorded_at TIMESTAMPTZ  NOT NULL DEFAULT now()
);
INSERT INTO calendar_coverage (year, source)
SELECT 2026, 'NSE equity segment holiday circular for 2026 (sql/07_trading_calendar.sql)'
WHERE EXISTS (SELECT 1 FROM market_holidays WHERE EXTRACT(YEAR FROM holiday_date) = 2026)
ON CONFLICT (year) DO NOTHING;

CREATE TABLE IF NOT EXISTS trading_suspensions (
    suspension_id  SERIAL       PRIMARY KEY,
    instrument_id  INTEGER      NOT NULL REFERENCES instruments(instrument_id),
    valid_from     DATE         NOT NULL,
    valid_to       DATE,
    reason         TEXT         NOT NULL,
    source         TEXT         NOT NULL,
    recorded_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);
CREATE INDEX IF NOT EXISTS idx_trading_suspensions_instrument ON trading_suspensions (instrument_id, valid_from);
CREATE INDEX IF NOT EXISTS idx_reconciliation_trade_date ON reconciliation_results (trade_date, instrument_id);
CREATE INDEX IF NOT EXISTS idx_instrument_symbols_instrument ON instrument_symbols (instrument_id, vendor, valid_from);

CREATE OR REPLACE FUNCTION session_final_at(p_day DATE)
RETURNS TIMESTAMPTZ
LANGUAGE sql IMMUTABLE
AS $$
    SELECT (p_day + TIME '16:30') AT TIME ZONE 'Asia/Kolkata';
$$;

CREATE OR REPLACE FUNCTION expected_sessions(p_from DATE, p_to DATE, p_universe TEXT DEFAULT 'dashboard_25')
RETURNS TABLE (universe TEXT, instrument_id INTEGER, ticker TEXT, session_date DATE)
LANGUAGE sql STABLE
AS $$
    SELECT m.universe::TEXT, m.instrument_id, s.symbol::TEXT, d::date
    FROM universe_membership m
    CROSS JOIN LATERAL generate_series(GREATEST(p_from, m.valid_from),
                                       LEAST(p_to, COALESCE(m.valid_to - 1, p_to)), INTERVAL '1 day') AS d
    LEFT JOIN LATERAL (
        SELECT x.symbol FROM instrument_symbols x
        WHERE x.instrument_id = m.instrument_id AND x.vendor = 'yahoo'
          AND x.valid_from <= d::date AND (x.valid_to IS NULL OR x.valid_to > d::date)
        ORDER BY x.valid_from DESC LIMIT 1
    ) s ON TRUE
    WHERE m.universe = p_universe AND EXTRACT(ISODOW FROM d) < 6;
$$;

CREATE OR REPLACE FUNCTION session_coverage(p_from DATE, p_to DATE, p_universe TEXT DEFAULT 'dashboard_25',
                                            p_as_of TIMESTAMPTZ DEFAULT now(),
                                            p_grace INTERVAL DEFAULT INTERVAL '14 hours 40 minutes')
RETURNS TABLE (universe TEXT, instrument_id INTEGER, ticker TEXT, session_date DATE, status TEXT, detail TEXT,
               final_at TIMESTAMPTZ, last_fetch_at TIMESTAMPTZ, last_fetch_status TEXT)
LANGUAGE sql STABLE
AS $$
    WITH pairs AS (
        SELECT e.*, session_final_at(e.session_date) AS final_at
        FROM expected_sessions(p_from, LEAST(p_to, (p_as_of AT TIME ZONE 'Asia/Kolkata')::date), p_universe) e
    ), joined AS MATERIALIZED (
        SELECT p.*, dp.source AS row_source, h.occasion
        FROM pairs p
        LEFT JOIN daily_prices dp ON dp.ticker = p.ticker AND dp.price_date = p.session_date
        LEFT JOIN market_holidays h ON h.holiday_date = p.session_date
    ), span AS MATERIALIZED (
        SELECT MIN(price_date) AS first_day, MAX(price_date) AS last_day FROM daily_prices
    ), bounds AS MATERIALIZED (
        SELECT d.ticker, MIN(d.price_date) AS first_day, MAX(d.price_date) AS last_day
        FROM daily_prices d GROUP BY d.ticker
    ), failed_runs AS MATERIALIZED (
        SELECT u.ticker, r.started_at
        FROM pipeline_runs r CROSS JOIN LATERAL unnest(r.tickers_failed) AS u(ticker)
        WHERE r.job_name = 'daily_update' AND r.started_at <= p_as_of AND cardinality(r.tickers_failed) > 0
    ), gaps AS (
        SELECT j.*,
               EXISTS (SELECT 1 FROM daily_prices a WHERE a.price_date = j.session_date) AS any_row,
               EXISTS (SELECT 1 FROM reconciliation_results r
                       WHERE r.trade_date = j.session_date AND r.bhav_close IS NOT NULL) AS bhav_file,
               EXISTS (SELECT 1 FROM calendar_coverage c WHERE c.year = EXTRACT(YEAR FROM j.session_date)::SMALLINT) AS calendar_known,
               b.first_day, b.last_day,
               sp.reason AS suspension_reason,
               (EXISTS (SELECT 1 FROM reconciliation_results r
                        WHERE r.trade_date = j.session_date AND r.bhav_close IS NOT NULL)
                AND NOT EXISTS (SELECT 1 FROM reconciliation_results r
                                WHERE r.trade_date = j.session_date AND r.instrument_id = j.instrument_id
                                  AND r.bhav_close IS NOT NULL)) AS absent_from_bhavcopy,
               f.retrieved_at AS last_fetch_at, f.status AS last_fetch_status,
               fr.started_at AS failed_run_at
        FROM joined j
        LEFT JOIN bounds b ON b.ticker = j.ticker
        LEFT JOIN LATERAL (
            SELECT s.reason FROM trading_suspensions s
            WHERE s.instrument_id = j.instrument_id AND s.valid_from <= j.session_date
              AND (s.valid_to IS NULL OR s.valid_to > j.session_date)
            ORDER BY s.valid_from DESC LIMIT 1
        ) sp ON TRUE
        LEFT JOIN LATERAL (
            SELECT l.retrieved_at, l.status::TEXT AS status FROM fetch_log l
            WHERE l.symbol = j.ticker AND l.endpoint = 'history:1d'
              AND l.retrieved_at >= j.final_at AND l.retrieved_at <= p_as_of
              AND (l.request->>'start' IS NULL OR (l.request->>'start')::date <= j.session_date)
            ORDER BY l.retrieved_at DESC LIMIT 1
        ) f ON TRUE
        LEFT JOIN LATERAL (
            SELECT MAX(x.started_at) AS started_at FROM failed_runs x
            WHERE x.ticker = j.ticker AND x.started_at >= j.final_at AND x.started_at < j.final_at + INTERVAL '10 days'
        ) fr ON TRUE
        WHERE j.row_source IS NULL AND j.occasion IS NULL
    ), classified AS (
        SELECT g.universe, g.instrument_id, g.ticker, g.session_date, g.final_at, g.last_fetch_at, g.last_fetch_status,
               CASE
                   WHEN NOT g.calendar_known AND NOT g.any_row AND NOT g.bhav_file
                        AND g.session_date BETWEEN s.first_day AND s.last_day THEN 'holiday'
                   WHEN g.suspension_reason IS NOT NULL OR g.absent_from_bhavcopy THEN 'suspended'
                   WHEN p_as_of < g.final_at THEN 'not_yet_published'
                   WHEN g.last_fetch_status IN ('error', 'parse_error', 'blocked')
                        OR g.failed_run_at >= COALESCE(g.last_fetch_at, '-infinity') THEN 'fetch_failed'
                   WHEN p_as_of < g.final_at + p_grace THEN 'not_yet_published'
                   ELSE 'missing'
               END AS status,
               CASE
                   WHEN NOT g.calendar_known AND NOT g.any_row AND NOT g.bhav_file
                        AND g.session_date BETWEEN s.first_day AND s.last_day
                       THEN 'inferred: no instrument has a price row and no bhavcopy row (calendar not loaded for this year)'
                   WHEN g.suspension_reason IS NOT NULL THEN 'trading_suspensions: ' || g.suspension_reason
                   WHEN g.absent_from_bhavcopy THEN 'inferred: absent from the NSE bhavcopy for this session'
                   WHEN p_as_of < g.final_at THEN 'session not final yet'
                   WHEN g.last_fetch_status IN ('error', 'parse_error', 'blocked')
                       THEN 'latest fetch after the close: ' || g.last_fetch_status
                   WHEN g.failed_run_at >= COALESCE(g.last_fetch_at, '-infinity')
                       THEN 'listed in tickers_failed of a daily_update run after the close'
                   WHEN p_as_of < g.final_at + p_grace THEN
                       CASE WHEN g.last_fetch_at IS NULL THEN 'not fetched since the close yet'
                            ELSE 'fetched after the close; source has no bar yet' END
                   WHEN g.first_day IS NULL OR g.session_date < g.first_day THEN 'before the first stored price for this ticker'
                   WHEN g.session_date > g.last_day THEN 'after the last stored price for this ticker'
                   WHEN g.last_fetch_at IS NULL THEN 'gap; no fetch recorded after the close'
                   ELSE 'gap; fetched after the close and no row stored'
               END AS detail
        FROM gaps g CROSS JOIN span s
    )
    SELECT j.universe, j.instrument_id, j.ticker, j.session_date,
           CASE WHEN j.row_source IS NOT NULL THEN 'present' ELSE 'holiday' END,
           CASE WHEN j.row_source IS NOT NULL AND j.occasion IS NOT NULL
                    THEN j.row_source || '; row on an NSE holiday (' || j.occasion || ')'
                WHEN j.row_source IS NOT NULL THEN j.row_source::TEXT
                ELSE 'nse_calendar: ' || j.occasion END,
           j.final_at, NULL::TIMESTAMPTZ, NULL::TEXT
    FROM joined j
    WHERE j.row_source IS NOT NULL OR j.occasion IS NOT NULL
    UNION ALL
    SELECT c.universe, c.instrument_id, c.ticker, c.session_date, c.status, c.detail, c.final_at, c.last_fetch_at,
           c.last_fetch_status
    FROM classified c;
$$;

CREATE OR REPLACE VIEW v_expected_sessions AS
SELECT * FROM expected_sessions(DATE '2015-01-01', (now() AT TIME ZONE 'Asia/Kolkata')::date, 'dashboard_25');

CREATE OR REPLACE VIEW v_session_coverage AS
SELECT * FROM session_coverage(DATE '2015-01-01', (now() AT TIME ZONE 'Asia/Kolkata')::date, 'dashboard_25');

CREATE OR REPLACE VIEW v_session_coverage_summary AS
WITH c AS (SELECT *, EXTRACT(YEAR FROM session_date)::INT AS year FROM v_session_coverage)
SELECT year, status, COUNT(*) AS pairs,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY year), 2) AS pct_of_year,
       COUNT(*) FILTER (WHERE detail = 'before the first stored price for this ticker') AS before_first_price,
       COUNT(DISTINCT ticker) AS tickers, MIN(session_date) AS first_session, MAX(session_date) AS last_session
FROM c
GROUP BY year, status
UNION ALL
SELECT NULL, status, COUNT(*), ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2),
       COUNT(*) FILTER (WHERE detail = 'before the first stored price for this ticker'), COUNT(DISTINCT ticker),
       MIN(session_date), MAX(session_date)
FROM c
GROUP BY status;

CREATE OR REPLACE VIEW v_session_coverage_by_ticker AS
SELECT ticker,
       COUNT(*) FILTER (WHERE status IN ('present', 'missing', 'fetch_failed')) AS expected_trading_sessions,
       COUNT(*) FILTER (WHERE status = 'present') AS present,
       COUNT(*) FILTER (WHERE status = 'missing') AS missing,
       COUNT(*) FILTER (WHERE status = 'fetch_failed') AS fetch_failed,
       COUNT(*) FILTER (WHERE status = 'holiday') AS holiday,
       COUNT(*) FILTER (WHERE status = 'suspended') AS suspended,
       COUNT(*) FILTER (WHERE status = 'not_yet_published') AS not_yet_published,
       ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'present')
             / NULLIF(COUNT(*) FILTER (WHERE status IN ('present', 'missing', 'fetch_failed')), 0), 3) AS coverage_pct,
       COUNT(*) FILTER (WHERE detail = 'before the first stored price for this ticker') AS before_first_price,
       ROUND(100.0 * COUNT(*) FILTER (WHERE status = 'present')
             / NULLIF(COUNT(*) FILTER (WHERE status IN ('present', 'missing', 'fetch_failed')
                                         AND detail IS DISTINCT FROM 'before the first stored price for this ticker'), 0), 3)
           AS coverage_since_first_price_pct,
       MIN(session_date) FILTER (WHERE status IN ('missing', 'fetch_failed')) AS first_gap,
       MAX(session_date) FILTER (WHERE status IN ('missing', 'fetch_failed')) AS last_gap
FROM v_session_coverage
GROUP BY ticker;

GRANT SELECT ON calendar_coverage, trading_suspensions, v_expected_sessions, v_session_coverage,
                v_session_coverage_summary, v_session_coverage_by_ticker TO readonly_access;
GRANT EXECUTE ON FUNCTION expected_sessions(DATE, DATE, TEXT),
                          session_coverage(DATE, DATE, TEXT, TIMESTAMPTZ, INTERVAL) TO readonly_access;

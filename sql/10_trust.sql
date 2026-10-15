-- =====================================================================
-- 10_trust.sql — a data-trust score for every stored price, and tables for the
-- signal-fragility study (src/fragility.py).
--
-- Trust score (0-100) of one stock-day, from four pieces of evidence:
--   official   40  agrees with the NSE official close (40) / only BSE checked and agrees (35)
--                  / no official file for that day (20, unverified) / disagrees (0)
--   stability  25  never revised (25) / revised once (15) / revised 2+ times (5);
--                  -10 if the last change is under 24 h old (not settled yet)
--   provenance 20  Yahoo daily candle (20) / rebuilt from hourly bars (8)
--   sanity     15  close inside the day's high-low range, volume > 0, OHLC present (5 each)
-- Grades: A >= 90, B >= 75, C >= 55, D below.
-- Safe to re-run.
-- =====================================================================

CREATE OR REPLACE VIEW v_price_trust AS
WITH ver AS (
    -- Only changes of the reported values count against trust. Dividend / split back-adjustment
    -- is Yahoo correctly rescaling history, not an error, so it is not counted here.
    SELECT ticker, price_date, COUNT(*) AS n_revisions, MAX(revised_at) AS last_change
    FROM v_price_revisions
    WHERE revision_kind NOT IN ('dividend_adjustment', 'split_or_bonus_adjustment', 'deleted')
    GROUP BY ticker, price_date
),
parts AS (
    SELECT p.ticker, p.price_date, p.close, p.source,
           n.close AS nse_close, b.close AS bse_close,
           COALESCE(v.n_revisions, 0) AS n_revisions, v.last_change,
           CASE WHEN n.close IS NOT NULL AND ABS(p.close - n.close) <= GREATEST(0.05, n.close * 0.0001) THEN 40
                WHEN n.close IS NULL AND b.close IS NOT NULL
                     AND ABS(p.close - b.close) <= GREATEST(0.05, b.close * 0.0001)               THEN 35
                -- no official file, or a later split/bonus rescaled Yahoo's history (ratio far from 1):
                -- cannot be compared directly, so unverified rather than wrong
                WHEN COALESCE(n.close, b.close) IS NULL
                     OR p.close / COALESCE(n.close, b.close) NOT BETWEEN 0.8 AND 1.25             THEN 20
                ELSE 0 END                                                      AS s_official,
           CASE WHEN COALESCE(v.n_revisions, 0) = 0 THEN 25
                WHEN v.n_revisions = 1 THEN 15 ELSE 5 END
             - CASE WHEN v.n_revisions > 0 AND v.last_change > now() - INTERVAL '24 hours' THEN 10 ELSE 0 END
                                                                                AS s_stability,
           CASE p.source WHEN 'yahoo_daily' THEN 20 ELSE 8 END                  AS s_provenance,
           (CASE WHEN p.high IS NOT NULL AND p.low IS NOT NULL
                      AND p.close BETWEEN p.low * 0.999 AND p.high * 1.001 THEN 5 ELSE 0 END
          + CASE WHEN COALESCE(p.volume, 0) > 0 THEN 5 ELSE 0 END
          + CASE WHEN p.open IS NOT NULL AND p.high IS NOT NULL AND p.low IS NOT NULL THEN 5 ELSE 0 END)
                                                                                AS s_sanity
    FROM daily_prices p
    LEFT JOIN reference_prices n ON n.source = 'NSE' AND n.ticker = p.ticker AND n.price_date = p.price_date
    LEFT JOIN reference_prices b ON b.source = 'BSE' AND b.ticker = p.ticker AND b.price_date = p.price_date
    LEFT JOIN ver v ON v.ticker = p.ticker AND v.price_date = p.price_date
)
SELECT ticker, price_date, close, source, nse_close, bse_close, n_revisions, last_change,
       s_official, GREATEST(s_stability, 0) AS s_stability, s_provenance, s_sanity,
       s_official + GREATEST(s_stability, 0) + s_provenance + s_sanity AS trust_score,
       CASE WHEN s_official + GREATEST(s_stability, 0) + s_provenance + s_sanity >= 90 THEN 'A'
            WHEN s_official + GREATEST(s_stability, 0) + s_provenance + s_sanity >= 75 THEN 'B'
            WHEN s_official + GREATEST(s_stability, 0) + s_provenance + s_sanity >= 55 THEN 'C'
            ELSE 'D' END AS trust_grade,
       array_remove(ARRAY[
           CASE WHEN s_official = 0  THEN 'disagrees_with_exchange' END,
           CASE WHEN s_official = 20 THEN 'not_verified' END,
           CASE WHEN n_revisions = 1 THEN 'revised_once' END,
           CASE WHEN n_revisions > 1 THEN 'revised_' || n_revisions || 'x' END,
           CASE WHEN n_revisions > 0 AND last_change > now() - INTERVAL '24 hours' THEN 'not_settled' END,
           CASE WHEN source <> 'yahoo_daily' THEN 'rebuilt_from_hourly' END,
           CASE WHEN s_sanity < 15 THEN 'failed_sanity_check' END
       ], NULL) AS trust_flags
FROM parts;

-- Trust of every stock's latest day (dashboard badge)
CREATE OR REPLACE VIEW v_latest_trust AS
SELECT DISTINCT ON (t.ticker) t.*, c.company_name
FROM v_price_trust t
JOIN companies c ON c.ticker = t.ticker
ORDER BY t.ticker, t.price_date DESC;

-- Grade distribution, all history and the most recent 30 sessions
CREATE OR REPLACE VIEW v_trust_summary AS
SELECT 'all_history' AS period, trust_grade, COUNT(*) AS n_rows,
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM v_price_trust GROUP BY trust_grade
UNION ALL
SELECT 'last_30_sessions', trust_grade, COUNT(*),
       ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)
FROM v_price_trust
WHERE price_date >= (SELECT MIN(d) FROM (SELECT DISTINCT price_date AS d FROM daily_prices
                                          ORDER BY price_date DESC LIMIT 30) x)
GROUP BY trust_grade;

-- ---------- Signal-fragility study (filled by src/fragility.py) ----------
CREATE TABLE IF NOT EXISTS fragility_runs (
    frag_run_id    BIGSERIAL   PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    run_id         BIGINT,                 -- pipeline_runs.run_id
    data_through   DATE,
    n_stock_days   INTEGER,
    seed           INTEGER,
    params         JSONB,                  -- scenario settings and calibrated error model
    validation     JSONB,                  -- how exactly the Python replica matches the SQL signals
    notes          TEXT
);

CREATE TABLE IF NOT EXISTS fragility_results (
    frag_run_id    BIGINT       NOT NULL REFERENCES fragility_runs(frag_run_id) ON DELETE CASCADE,
    scenario       VARCHAR(40)  NOT NULL,
    signal         VARCHAR(40)  NOT NULL,
    n_compared     INTEGER      NOT NULL CHECK (n_compared >= 0),
    n_flipped      INTEGER      NOT NULL CHECK (n_flipped >= 0),
    flip_rate      NUMERIC(9,6),
    ci_low         NUMERIC(9,6),           -- Wilson 95% interval
    ci_high        NUMERIC(9,6),
    PRIMARY KEY (frag_run_id, scenario, signal),
    CHECK (n_flipped <= n_compared)
);

CREATE TABLE IF NOT EXISTS backtest_sensitivity (
    frag_run_id    BIGINT       NOT NULL REFERENCES fragility_runs(frag_run_id) ON DELETE CASCADE,
    scenario       VARCHAR(40)  NOT NULL,
    metric         VARCHAR(60)  NOT NULL,
    value          NUMERIC,
    PRIMARY KEY (frag_run_id, scenario, metric)
);

CREATE OR REPLACE VIEW v_fragility_latest AS
SELECT r.scenario, r.signal, r.n_compared, r.n_flipped,
       ROUND(r.flip_rate * 100, 3) AS flip_pct,
       ROUND(r.ci_low * 100, 3)    AS ci_low_pct,
       ROUND(r.ci_high * 100, 3)   AS ci_high_pct,
       f.created_at, f.data_through
FROM fragility_results r
JOIN fragility_runs f ON f.frag_run_id = r.frag_run_id
WHERE r.frag_run_id = (SELECT MAX(frag_run_id) FROM fragility_runs)
ORDER BY r.scenario, r.flip_rate DESC;

GRANT SELECT ON v_price_trust, v_latest_trust, v_trust_summary, fragility_runs, fragility_results,
                backtest_sensitivity, v_fragility_latest TO readonly_access;

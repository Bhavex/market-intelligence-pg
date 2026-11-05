CREATE TABLE IF NOT EXISTS backtest_trials (
    trial_id          BIGSERIAL     PRIMARY KEY,
    registered_at     TIMESTAMPTZ   NOT NULL DEFAULT clock_timestamp(),
    run_tag           VARCHAR(40)   NOT NULL,
    code_version      VARCHAR(64)   NOT NULL,
    source            VARCHAR(16)   NOT NULL,
    data_fingerprint  CHAR(64)      NOT NULL,
    data_start        DATE          NOT NULL,
    data_end          DATE          NOT NULL,
    strategy          VARCHAR(40)   NOT NULL,
    params            JSONB         NOT NULL,
    period            VARCHAR(12)   NOT NULL CHECK (period IN ('full', 'development', 'holdout')),
    period_start      DATE          NOT NULL,
    period_end        DATE          NOT NULL,
    cost_level        VARCHAR(16)   NOT NULL,
    cost_bp           NUMERIC(8,3)  NOT NULL,
    n_obs             INTEGER       NOT NULL CHECK (n_obs > 0),
    metrics           JSONB         NOT NULL,
    purpose           VARCHAR(32)   NOT NULL,
    selected          BOOLEAN       NOT NULL DEFAULT FALSE,
    CHECK (period_end >= period_start)
);
CREATE INDEX IF NOT EXISTS idx_backtest_trials_strategy ON backtest_trials (strategy, period, registered_at);

DROP TRIGGER IF EXISTS backtest_trials_append_only ON backtest_trials;
CREATE TRIGGER backtest_trials_append_only
BEFORE UPDATE OR DELETE ON backtest_trials
FOR EACH ROW EXECUTE FUNCTION forbid_mutation();

CREATE OR REPLACE VIEW v_backtest_trial_counts AS
SELECT strategy, source,
       COUNT(*) AS evaluations,
       COUNT(DISTINCT params) AS distinct_parameter_sets,
       COUNT(*) FILTER (WHERE period = 'holdout') AS holdout_evaluations,
       COUNT(DISTINCT run_tag) AS runs,
       MIN(registered_at) AS first_evaluated,
       MAX(registered_at) AS last_evaluated
FROM backtest_trials
GROUP BY strategy, source;

CREATE OR REPLACE VIEW v_backtest_holdout_looks AS
SELECT run_tag, MIN(registered_at) AS registered_at, source, MIN(period_start) AS holdout_start,
       MAX(period_end) AS holdout_end, COUNT(*) AS holdout_evaluations,
       COUNT(*) FILTER (WHERE selected) AS selected_evaluations
FROM backtest_trials
WHERE period = 'holdout'
GROUP BY run_tag, source;

GRANT SELECT ON backtest_trials, v_backtest_trial_counts, v_backtest_holdout_looks TO readonly_access;

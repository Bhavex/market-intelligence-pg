-- =====================================================================
-- 12_ml.sql — next-day direction prediction: training runs and daily predictions
-- (src/ml_features.py, src/ml_train.py, src/ml_predict.py). Safe to re-run.
-- =====================================================================

CREATE TABLE IF NOT EXISTS model_runs (
    run_id          BIGSERIAL    PRIMARY KEY,
    model_name      VARCHAR(30)  NOT NULL,
    run_kind        VARCHAR(20)  NOT NULL DEFAULT 'holdout'
                    CHECK (run_kind IN ('holdout', 'walk_forward', 'production')),
    trained_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    train_start     DATE         NOT NULL,
    train_end       DATE         NOT NULL,
    test_start      DATE,
    test_end        DATE,
    params          JSONB        NOT NULL DEFAULT '{}',
    feature_names   TEXT[]       NOT NULL DEFAULT '{}',
    n_train         INTEGER,
    n_test          INTEGER,
    accuracy        NUMERIC(6,4),
    balanced_acc    NUMERIC(6,4),
    f1              NUMERIC(6,4),
    baseline_acc    NUMERIC(6,4),
    strategy_return NUMERIC(10,6),
    buyhold_return  NUMERIC(10,6),
    notes           TEXT,
    pipeline_run_id BIGINT       REFERENCES pipeline_runs(run_id)
);

CREATE TABLE IF NOT EXISTS predictions (
    ticker          VARCHAR(20)  NOT NULL REFERENCES companies(ticker) ON UPDATE CASCADE,
    feature_date    DATE         NOT NULL,
    target_date     DATE         NOT NULL,
    model_name      VARCHAR(30)  NOT NULL,
    run_id          BIGINT       REFERENCES model_runs(run_id),
    predicted_up    BOOLEAN      NOT NULL,
    prob_up         NUMERIC(6,4) CHECK (prob_up BETWEEN 0 AND 1),
    actual_up       BOOLEAN,
    actual_return   NUMERIC(10,6),
    predicted_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    PRIMARY KEY (ticker, feature_date, model_name),
    CHECK (target_date > feature_date)
);

CREATE INDEX IF NOT EXISTS idx_predictions_target ON predictions (target_date);
CREATE INDEX IF NOT EXISTS idx_predictions_model  ON predictions (model_name, feature_date DESC);
CREATE INDEX IF NOT EXISTS idx_model_runs_model   ON model_runs (model_name, trained_at DESC);

CREATE OR REPLACE VIEW v_model_scoreboard AS
SELECT model_name,
       COUNT(*) FILTER (WHERE actual_up IS NOT NULL)                              AS n_resolved,
       COUNT(*) FILTER (WHERE actual_up IS NULL)                                  AS n_pending,
       ROUND(AVG((predicted_up = actual_up)::int) FILTER (WHERE actual_up IS NOT NULL), 4) AS live_accuracy,
       MIN(feature_date)                                                          AS first_prediction,
       MAX(feature_date)                                                          AS last_prediction
FROM predictions
GROUP BY model_name;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'readonly_access') THEN
        GRANT SELECT ON model_runs, predictions, v_model_scoreboard TO readonly_access;
    END IF;
END $$;

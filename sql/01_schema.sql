-- =====================================================================
-- Sector-Wise Market Intelligence & Signal Dashboard
-- 01_schema.sql  —  core tables (PostgreSQL 15+)
-- Safe to re-run: every object uses IF NOT EXISTS.
-- =====================================================================

-- ---------- Reference / dimension tables ----------

CREATE TABLE IF NOT EXISTS sectors (
    sector_id     SMALLSERIAL PRIMARY KEY,
    sector_name   VARCHAR(50)  NOT NULL UNIQUE,
    nifty_index   VARCHAR(50),                       -- e.g. 'NIFTY IT'
    description   TEXT
);

CREATE TABLE IF NOT EXISTS cities (
    city_id       SMALLSERIAL PRIMARY KEY,
    city_name     VARCHAR(60)  NOT NULL,
    state         VARCHAR(60)  NOT NULL,
    region        VARCHAR(10)  NOT NULL CHECK (region IN ('North','South','East','West','Central')),
    latitude      NUMERIC(9,6) NOT NULL CHECK (latitude  BETWEEN -90  AND 90),
    longitude     NUMERIC(9,6) NOT NULL CHECK (longitude BETWEEN -180 AND 180),
    UNIQUE (city_name, state)
);

CREATE TABLE IF NOT EXISTS companies (
    ticker        VARCHAR(20)  PRIMARY KEY,          -- Yahoo symbol, e.g. 'INFY.NS'
    company_name  VARCHAR(120) NOT NULL,
    sector_id     SMALLINT     NOT NULL REFERENCES sectors(sector_id),
    hq_city_id    SMALLINT     NOT NULL REFERENCES cities(city_id),
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE, -- set FALSE instead of deleting (keeps history)
    added_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

-- ---------- Fact tables ----------

CREATE TABLE IF NOT EXISTS daily_prices (
    ticker        VARCHAR(20)   NOT NULL REFERENCES companies(ticker) ON UPDATE CASCADE,
    price_date    DATE          NOT NULL,
    open          NUMERIC(14,4),
    high          NUMERIC(14,4),
    low           NUMERIC(14,4),
    close         NUMERIC(14,4) NOT NULL CHECK (close > 0),
    adj_close     NUMERIC(14,4),
    volume        BIGINT        CHECK (volume >= 0),
    loaded_at     TIMESTAMPTZ   NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, price_date),
    CHECK (high IS NULL OR low IS NULL OR high >= low)
);

CREATE TABLE IF NOT EXISTS signals (
    ticker            VARCHAR(20) NOT NULL,
    signal_date       DATE        NOT NULL,
    return_1d         NUMERIC(10,6),   -- daily % change as a fraction (0.012 = +1.2%)
    momentum_20d      NUMERIC(10,6),   -- 20-trading-day return
    momentum_60d      NUMERIC(10,6),   -- 60-trading-day return
    volatility_20d    NUMERIC(10,6),   -- annualised stdev of daily returns, 20-day window
    sma_50            NUMERIC(14,4),
    sma_200           NUMERIC(14,4),
    rsi_14            NUMERIC(6,2),    -- 0–100, simple-average RSI
    volume_zscore_20d NUMERIC(10,4),   -- today's volume vs prior 20 days
    trend             VARCHAR(10) CHECK (trend IN ('UP','DOWN','NEUTRAL')),
    is_anomaly        BOOLEAN     NOT NULL DEFAULT FALSE,
    anomaly_reason    VARCHAR(40),
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, signal_date),
    FOREIGN KEY (ticker, signal_date) REFERENCES daily_prices(ticker, price_date) ON DELETE CASCADE
);

-- 52-week breakout signals (added later, so ALTER ... IF NOT EXISTS: safe on existing databases).
-- All four are NULL until a stock has 252 prior trading days of adj_close. See README "Signals".
ALTER TABLE signals
    ADD COLUMN IF NOT EXISTS high_52w          NUMERIC(12,2),  -- max adj_close of the previous 252 trading days (today excluded)
    ADD COLUMN IF NOT EXISTS breakout_52w      BOOLEAN,        -- adj_close > high_52w
    ADD COLUMN IF NOT EXISTS pct_from_52w_high NUMERIC(8,2),   -- (adj_close / high_52w - 1) * 100; negative below the high, positive on a breakout
    ADD COLUMN IF NOT EXISTS strong_breakout   BOOLEAN;        -- breakout_52w AND volume_zscore_20d > 2

-- Fundamentals are stored as dated snapshots, so history builds up over time.
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker          VARCHAR(20) NOT NULL REFERENCES companies(ticker),
    snapshot_date   DATE        NOT NULL DEFAULT CURRENT_DATE,
    market_cap      NUMERIC(20,2),
    total_revenue   NUMERIC(20,2),
    eps_ttm         NUMERIC(12,4),
    pe_ratio        NUMERIC(12,4),
    price_to_book   NUMERIC(12,4),
    dividend_yield  NUMERIC(8,4),
    PRIMARY KEY (ticker, snapshot_date)
);

-- ---------- Pipeline monitoring ----------

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id         BIGSERIAL   PRIMARY KEY,
    job_name       VARCHAR(40) NOT NULL,           -- 'backfill' | 'daily_update' | 'fundamentals'
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         VARCHAR(10) NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running','success','partial','failed')),
    rows_upserted  INTEGER     NOT NULL DEFAULT 0,
    tickers_failed TEXT[]      NOT NULL DEFAULT '{}',
    message        TEXT
);

-- ---------- Indexes ----------
-- (ticker, date) lookups are already covered by the primary keys.
CREATE INDEX IF NOT EXISTS idx_daily_prices_date   ON daily_prices (price_date);
CREATE INDEX IF NOT EXISTS idx_signals_date        ON signals (signal_date);
CREATE INDEX IF NOT EXISTS idx_signals_anomaly     ON signals (signal_date) WHERE is_anomaly;
CREATE INDEX IF NOT EXISTS idx_companies_sector    ON companies (sector_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_job   ON pipeline_runs (job_name, started_at DESC);

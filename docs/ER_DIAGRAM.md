# ER Diagram

```mermaid
erDiagram
    SECTORS   ||--o{ COMPANIES     : "classifies"
    CITIES    ||--o{ COMPANIES     : "headquarters of"
    COMPANIES ||--o{ DAILY_PRICES  : "has"
    COMPANIES ||--o{ FUNDAMENTALS  : "has snapshots"
    DAILY_PRICES ||--o| SIGNALS    : "derives"

    SECTORS {
        smallint sector_id PK
        varchar  sector_name UK
        varchar  nifty_index
        text     description
    }
    CITIES {
        smallint city_id PK
        varchar  city_name
        varchar  state
        varchar  region
        numeric  latitude
        numeric  longitude
    }
    COMPANIES {
        varchar  ticker PK
        varchar  company_name
        smallint sector_id FK
        smallint hq_city_id FK
        boolean  is_active
    }
    DAILY_PRICES {
        varchar ticker PK, FK
        date    price_date PK
        numeric open
        numeric high
        numeric low
        numeric close
        numeric adj_close
        bigint  volume
    }
    SIGNALS {
        varchar ticker PK, FK
        date    signal_date PK, FK
        numeric return_1d
        numeric momentum_20d
        numeric momentum_60d
        numeric volatility_20d
        numeric sma_50
        numeric sma_200
        numeric rsi_14
        numeric volume_zscore_20d
        varchar trend
        boolean is_anomaly
    }
    FUNDAMENTALS {
        varchar ticker PK, FK
        date    snapshot_date PK
        numeric market_cap
        numeric total_revenue
        numeric eps_ttm
        numeric pe_ratio
    }
    PIPELINE_RUNS {
        bigint      run_id PK
        varchar     job_name
        timestamptz started_at
        varchar     status
        integer     rows_upserted
    }
    AUDIT_LOG {
        bigint      audit_id PK
        varchar     table_name
        varchar     operation
        text        row_key
        jsonb       old_data
        jsonb       new_data
        timestamptz changed_at
    }
```

## Normalisation notes

- **3NF**: sector and city attributes live in their own tables; `companies` only stores foreign keys, so a city's coordinates or a sector's index name is stored exactly once.
- `daily_prices` and `signals` share the composite key `(ticker, date)`. `signals` has a composite foreign key to `daily_prices`, so a signal can never exist without its price row.
- `fundamentals` uses `(ticker, snapshot_date)` so repeated snapshots build a history instead of overwriting.
- `audit_log` is filled only by triggers (`trg_audit`), so every change to companies, fundamentals and price corrections is traceable.
- `pipeline_runs` is a standalone audit/log table (no FKs) used for pipeline monitoring.

## Constraints used

| Kind | Where |
|---|---|
| CHECK | `close > 0`, `high >= low`, `volume >= 0`, `region IN (...)`, lat/long ranges, `trend IN (...)`, `status IN (...)` |
| UNIQUE | `sectors.sector_name`, `cities(city_name, state)` |
| ON DELETE CASCADE | `signals → daily_prices` |
| Partial index | `idx_signals_anomaly ... WHERE is_anomaly` |

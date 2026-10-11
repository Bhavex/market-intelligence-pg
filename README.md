# Sector-Wise Market Intelligence & Signal Dashboard

DBMS course project (VIT). 25 NSE large-caps across 5 sectors (IT, Banking, Auto, Pharma, FMCG) are stored in **PostgreSQL** (hosted on **Neon**), turned into trading signals with SQL window functions, and visualised in **Power BI** / Excel.

> **Data scope:** daily end-of-day prices (updated automatically after market close on weekdays) plus history from 2015. This is *not* a real-time feed.

## Architecture

```
Yahoo Finance (yfinance)
        │  Python (src/)
        ▼
PostgreSQL on Neon ── refresh_signals() SQL function ── reporting views (v_*)
        ▲                                                     │
GitHub Actions (weekdays 5 PM IST)                Power BI / Excel dashboards
```

## Repository layout

| Path | What it is |
|---|---|
| `sql/01_schema.sql` | Tables, keys, constraints, indexes |
| `sql/02_seed.sql` | Sectors, HQ cities (with lat/long), 25 companies |
| `sql/03_signals.sql` | `refresh_signals(from_date)` — momentum, volatility, SMA, RSI, volume z-score, trend, anomalies |
| `sql/04_views.sql` | Views for Power BI: `v_company_daily`, `v_latest_snapshot`, `v_sector_daily`, `v_sector_strength`, `v_region_daily`, `v_anomalies`, `v_pipeline_health` |
| `sql/05_audit_and_procedures.sql` | Audit log + triggers, validation trigger, `replace_company()` procedure, read-only role |
| `demo/viva_queries.sql` | 14 demo queries: joins, GROUP BY/HAVING, subqueries, window functions, CTEs |
| `demo/transactions_demo.sql` | ACID demo: atomicity, constraints, savepoints, procedures, isolation levels |
| `src/setup_db.py` | Runs all SQL files (safe to re-run) |
| `src/backfill.py` | One-time history load from 2015 |
| `src/daily_update.py` | Incremental daily load + signal refresh |
| `src/fundamentals.py` | Weekly fundamentals snapshot |
| `.github/workflows/` | Daily + weekly automation |
| `docs/ER_DIAGRAM.md` | ER diagram and normalisation notes |

## Setup (Mac)

```bash
git clone https://github.com/Bhavex/market-intelligence-pg.git
cd market-intelligence-pg
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then paste the Neon connection string into .env
python src/setup_db.py        # create tables, seed data, functions, views
python src/backfill.py        # load history (takes a few minutes)
```

Daily automation needs one GitHub secret: **Settings → Secrets and variables → Actions → `DATABASE_URL`**.

## Signals (computed in SQL)

| Column | Meaning |
|---|---|
| `return_1d` | Daily return (fraction) |
| `momentum_20d`, `momentum_60d` | 20 / 60 trading-day return |
| `volatility_20d` | Annualised stdev of daily returns (20-day) |
| `sma_50`, `sma_200` | Simple moving averages (SMA-200 is NULL until 200 days exist) |
| `rsi_14` | 14-day RSI (simple-average version) |
| `volume_zscore_20d` | Today's volume vs. the previous 20 days |
| `trend` | `UP` if price > SMA50 > SMA200, `DOWN` if reverse, else `NEUTRAL` |
| `is_anomaly` | Move > 3σ of the prior 20 days, or volume z-score > 3 |

Prices use `adj_close` (split/dividend-adjusted) when available.

## DBMS features used

| Concept | Where |
|---|---|
| Normalisation (3NF), PK / FK / composite keys | `sql/01_schema.sql`, `docs/ER_DIAGRAM.md` |
| CHECK / UNIQUE / NOT NULL constraints | `sql/01_schema.sql` |
| Indexes (incl. partial index) | `sql/01_schema.sql` |
| Views | `sql/04_views.sql` |
| Stored function (PL/pgSQL, window functions, UPSERT) | `refresh_signals()` in `sql/03_signals.sql` |
| Stored procedure (atomic multi-step change, row locking) | `replace_company()` in `sql/05_…` |
| Triggers (AFTER audit, BEFORE validation, conditional `WHEN`) | `sql/05_…` |
| Audit trail (JSONB old/new values) | `audit_log`, `v_recent_changes` |
| Roles & privileges (read-only access) | `readonly_access` role in `sql/05_…` |
| Transactions, savepoints, isolation levels | `demo/transactions_demo.sql` |

## Read-only access for teammates / Power BI

Create the login **with SQL** (roles made in the Neon Console UI get admin rights, so they would not be read-only).
In the Neon SQL Editor run, choosing your own long password:

```sql
CREATE ROLE powerbi_reader WITH LOGIN PASSWORD 'choose-a-long-random-password-here';
GRANT readonly_access TO powerbi_reader;
```

Teammates / Power BI connect as `powerbi_reader`: they can `SELECT` everything but cannot change or delete data.

## Useful queries

```sql
SELECT * FROM v_pipeline_health;                         -- is the pipeline healthy?
SELECT * FROM v_sector_strength WHERE price_date = (SELECT MAX(price_date) FROM daily_prices);
SELECT * FROM v_anomalies LIMIT 20;
SELECT refresh_signals('2024-01-01');                    -- recompute signals from a date
```

## Team roles

- **Database & Pipeline Engineer** (Bhavishya): schema, backfill, automation, monitoring, ER diagram
- **Analytics**: signal logic and validation
- **Visualisation**: Power BI dashboards and regional map

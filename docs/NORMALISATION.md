# Design theory: ER mapping, functional dependencies, normalisation, relational algebra

## 1. ER model → relational schema

| ER element | Type | Mapped to |
|---|---|---|
| Sector | Strong entity | `sectors(sector_id PK, sector_name UNIQUE, nifty_index, description)` |
| City | Strong entity | `cities(city_id PK, city_name, state, region, latitude, longitude)`, `UNIQUE(city_name, state)` |
| Company | Strong entity | `companies(ticker PK, company_name, sector_id FK, hq_city_id FK, is_active)` |
| *belongs to* (Company N : 1 Sector) | Relationship | FK `companies.sector_id → sectors` (the "many" side holds the FK) |
| *headquartered in* (Company N : 1 City) | Relationship | FK `companies.hq_city_id → cities` |
| Daily price | **Weak entity** of Company (identified by ticker + date) | `daily_prices(ticker FK, price_date, …)`, PK `(ticker, price_date)` |
| Signal | 1 : 1 with a daily price (derived) | `signals` with PK = FK `(ticker, signal_date) → daily_prices` |
| Fundamentals snapshot | Weak entity of Company | `fundamentals`, PK `(ticker, snapshot_date)` |
| Pipeline run, Audit entry | Independent log entities | `pipeline_runs`, `audit_log` (surrogate keys) |

Participation: every company **must** have a sector and a city (total participation → `NOT NULL` FKs); a sector or city may exist with no companies (partial participation).

## 2. Functional dependencies

Let *D* = the attributes of the whole dataset in one flat table (what a naïve CSV would look like):

`D(ticker, company_name, sector_name, nifty_index, city_name, state, region, latitude, longitude, price_date, open, high, low, close, adj_close, volume, momentum_20d, rsi_14, …)`

| # | Functional dependency | Why it holds |
|---|---|---|
| FD1 | `ticker → company_name, sector_name, city_name, state` | a listed symbol has one name, one sector, one HQ |
| FD2 | `sector_name → nifty_index` | each sector maps to one NIFTY index |
| FD3 | `(city_name, state) → region, latitude, longitude` | a city's location is fixed |
| FD4 | `(ticker, price_date) → open, high, low, close, adj_close, volume` | one bar per stock per day |
| FD5 | `(ticker, price_date) → momentum_20d, rsi_14, sma_50, …` | signals are computed per stock per day |
| FD6 | `(ticker, snapshot_date) → market_cap, pe_ratio, …` | one snapshot per stock per date |

**Candidate key of D:** `(ticker, price_date)` — its closure contains every attribute (via FD1–FD5).

## 3. Normalisation, step by step

**1NF** — all attributes are atomic (one value per cell, no repeating groups such as "price_day1, price_day2, …"). The flat table *D* is already 1NF.

**2NF** — no non-key attribute may depend on *part* of a composite key.
*D* violates this: `company_name`, `sector_name`, `city_name` depend only on `ticker` (part of the key) — FD1 is a **partial dependency**.
→ Decompose: `company(ticker, company_name, sector_name, nifty_index, city_name, state, region, latitude, longitude)` and `price(ticker, price_date, open, high, low, close, adj_close, volume, signals…)`.

**3NF** — no non-key attribute may depend on another non-key attribute (no **transitive** dependency).
`company` violates this: `ticker → sector_name → nifty_index` (FD2) and `ticker → (city_name, state) → latitude, longitude` (FD3).
→ Decompose: `sectors(sector_id, sector_name, nifty_index)`, `cities(city_id, city_name, state, region, latitude, longitude)`, `companies(ticker, company_name, sector_id, hq_city_id)`.

**BCNF** — for every non-trivial FD X → Y, X is a superkey. In the final schema every determinant is a key of its own table (`sector_id`, `sector_name`, `city_id`, `(city_name,state)`, `ticker`, `(ticker, price_date)`, `(ticker, snapshot_date)`), so all tables are in **BCNF**.

**Lossless & dependency-preserving:** each decomposition splits on a key of one side (e.g. `ticker` is the key of `companies`), so natural joins rebuild *D* exactly; FD1–FD6 are each enforced inside a single table.

### Deliberate design choice: signals in a separate table
`price` (2NF result) was further split into `daily_prices` (raw facts) and `signals` (derived values). Both have key `(ticker, date)`, so this is not required by 3NF; it keeps **source data separate from computed data**: signals can be dropped and recomputed by `refresh_signals()` without touching the raw prices, and a composite FK guarantees no signal exists without its price row.

### Controlled redundancy
`mv_sector_daily` (materialized view) stores pre-aggregated data — intentional, read-only redundancy for speed, refreshed after each load. It is never written to directly, so it cannot create update anomalies.

## 4. Anomalies the design prevents

| Anomaly (in flat table *D*) | Prevented because |
|---|---|
| **Update**: renaming "Infosys" means editing ~2,900 rows | name stored once in `companies` |
| **Insert**: can't add a new sector until a stock in it has a price | `sectors` is independent |
| **Delete**: deleting a stock's last price row loses its sector & city | company data lives in `companies`; `is_active = FALSE` instead of DELETE |

## 5. Relational algebra for key queries

Notation: σ select, π project, ⋈ natural join, γ grouping/aggregation, ρ rename.

**Q1. Companies in the IT sector with their HQ city**

π<sub>ticker, company_name, city_name</sub> ( σ<sub>sector_name = 'IT'</sub> ( companies ⋈ sectors ⋈ cities ) )

**Q2. Average closing price per sector in 2025**

γ<sub>sector_name; AVG(close) → avg_close</sub> ( σ<sub>price_date ≥ 2025-01-01 ∧ price_date < 2026-01-01</sub> ( daily_prices ⋈ companies ⋈ sectors ) )

**Q3. Tickers that had an anomaly but no fundamentals snapshot** (set difference)

π<sub>ticker</sub> ( σ<sub>is_anomaly = true</sub> ( signals ) ) − π<sub>ticker</sub> ( fundamentals )

**Q4. Stocks whose latest close is above their own all-time average** (join with an aggregated relation)

L ← ρ<sub>L</sub> ( γ<sub>ticker; MAX(price_date) → d</sub> ( daily_prices ) )
A ← ρ<sub>A</sub> ( γ<sub>ticker; AVG(close) → avg_close</sub> ( daily_prices ) )
π<sub>ticker, close</sub> ( σ<sub>close > avg_close</sub> ( (daily_prices ⋈<sub>ticker = L.ticker ∧ price_date = L.d</sub> L) ⋈ A ) )

**Q5. Regions with more than 3 company headquarters**

σ<sub>n > 3</sub> ( γ<sub>region; COUNT(ticker) → n</sub> ( companies ⋈<sub>hq_city_id = city_id</sub> cities ) )

The SQL versions of these queries are in `demo/viva_queries.sql`.

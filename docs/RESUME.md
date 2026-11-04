# Resume and portfolio material

Every number below was measured in this repository; the source of each is listed at the end. Re-check them before
you use them, since production row counts grow every trading day.

## One-line description

> A self-updating PostgreSQL market-data warehouse for 25 NSE large caps. Signals are computed in SQL, the pipeline is
> automated with GitHub Actions, and a Streamlit dashboard and a read-only FastAPI service sit on top. The indexing
> strategy is measured, and the backtest results are reported honestly.

Shorter, for a projects list:

> **NSE Sector Intelligence:** PostgreSQL · PL/pgSQL · Python · GitHub Actions · Streamlit · FastAPI · Docker

## Resume bullets

- **Designed and automated a PostgreSQL 17 (Neon) warehouse** for 25 NSE large caps across 5 sectors.
  - ~212,500 daily price rows and as many SQL-computed signal rows since 2015.
  - Loaded three times every trading day by GitHub Actions, with pre-insert validation, idempotent upserts and 10
    data-quality gates.
  - Failed runs automatically open or update a GitHub issue.
- **Built the signal engine inside the database:** one PL/pgSQL function using window functions computes momentum,
  RSI, SMA-50/200, volatility, volume z-scores and 52-week breakouts for every stock. It matches an independent pandas
  implementation exactly on every row tested in CI. It is backed by a BCNF schema with CHECK and FK constraints, audit
  triggers and a least-privilege read-only role.
- **Cut query latency with measured index design** (`EXPLAIN ANALYZE` on 259,600 rows):
  - paginated API requests: 0.05 ms against 21.5 ms without the primary key (414×);
  - sector aggregates: 216 ms → 0.21 ms through a materialized view;
  - a monitoring view rewritten with `LATERAL` lookups: 31× faster (20.2 → 0.65 ms);
  - weekly benchmarks on production show 131–573× gains from the primary-key, partial and materialized-view designs.
- **Shipped a Streamlit dashboard and a read-only FastAPI service** on a shared data layer that runs queries in
  read-only transactions, with a demo mode and a one-command Docker Compose stack. It is covered by 267 automated
  tests in CI on Python 3.12 and 3.13 against a PostgreSQL 17 container.

**Optional fifth bullet (analytics roles):**
- **Backtested the momentum and sector-strength signals** without look-ahead bias, with next-session execution and
  11.2 bp NSE delivery costs. Over Feb 2015 – Oct 2026 the sector rotation ended at ₹359 against ₹350 for
  buy-and-hold, but with a lower Sharpe ratio (0.62 vs 0.84) and a deeper drawdown (−31.6% vs −19.4%). I reported
  that the signal does not beat the benchmark on a risk-adjusted basis.

## LinkedIn post draft

> I've been building a database project for my DBMS course, and it grew into something I'm proud of.
>
> **NSE Sector Intelligence** is a PostgreSQL warehouse that tracks 25 large-cap NSE stocks across IT, Banking, Auto,
> Pharma and FMCG, and keeps itself up to date. Every trading day, GitHub Actions does the following:
> - fetches the prices;
> - validates every row before it touches the database;
> - upserts the rows idempotently;
> - runs a PL/pgSQL signal engine (momentum, RSI, moving averages, 52-week breakouts);
> - checks data quality, and opens a GitHub issue by itself if something breaks.
>
> A few things I learned:
>
> 🔹 **Measure indexes, don't guess.** At production size, the composite primary key makes paginated API calls 414×
> faster (0.05 ms vs 21.5 ms). A materialized view takes a sector report from 216 ms to 0.21 ms. Measuring also turned
> up a slow monitoring view that I rewrote: 20 ms → 0.65 ms.
>
> 🔹 **Let the database enforce the rules.** CHECK and FK constraints, audit triggers and a read-only role catch bad
> data and mistakes that application code would miss.
>
> 🔹 **Be honest about results.** I backtested the sector-strength signal with realistic trading costs. It barely
> matched buy-and-hold (₹359 vs ₹350 over 11 years) while taking much more risk (Sharpe 0.62 vs 0.84). Not the
> exciting answer, but the true one.
>
> On top of the database there is a Streamlit dashboard, a read-only FastAPI service, a Docker Compose setup that runs
> everything with one command, and 267 automated tests.
>
> Code and write-ups: https://github.com/Bhavex/market-intelligence-pg
>
> #PostgreSQL #DataEngineering #Python #SQL #FinTech

## Where each number comes from

| Number | Source |
|---|---|
| 25 tickers, 5 sectors | `sql/02_seed.sql` |
| ~212,500 price rows and as many signal rows (production) | `docs/BENCHMARKS.md`, table sizes measured on Neon (212,507 rows each, 1 Nov 2026) |
| Three loads per trading day | `.github/workflows/daily_update.yml` schedule |
| 10 data-quality checks | `CHECKS` in `src/post_load.py` |
| Signal engine matches pandas exactly | `tests/test_db_integration.py::test_refresh_signals_matches_the_independent_pandas_engine` (640 rows, 100% on trend, anomaly, breakout, RSI and momentum) |
| 414×, 0.05 ms vs 21.5 ms; 216 → 0.21 ms; 20.2 → 0.65 ms | `docs/PERFORMANCE.md`, `results/performance/production_scale_100_tickers*.json` (local PostgreSQL 16, synthetic data, 259,600 rows) |
| 131–573× on production | `docs/BENCHMARKS.md` (Neon, PostgreSQL 17: primary key 131.5×, partial index 169×, materialized view 573×) |
| 267 tests | `python -m pytest --collect-only -q` (218 run without a database) |
| ₹359 vs ₹350, Sharpe 0.62 vs 0.84, drawdown −31.6% vs −19.4%, 11.2 bp | `docs/BACKTEST.md`, `results/backtest/published_rotation.json` |

The 414×, 216 ms and 31× figures come from synthetic data at production size on a local machine; the 131–573×
figures come from the real production database. Say which is which if an interviewer asks.

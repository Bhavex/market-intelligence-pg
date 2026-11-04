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
  - ~212,500 daily price rows and as many SQL-computed signal rows since 2015 across 81 stocks (the 25-stock
    dashboard universe, about 72,700 rows, plus a research universe).
  - Loaded three times every trading day by GitHub Actions, with pre-insert validation, idempotent upserts and 10
    data-quality gates.
  - Every fetch is recorded in an append-only ledger. Every observed value is kept with an as-of query, and quality
    decisions are timestamped, so history can be replayed as it was known.
  - Failed runs automatically open or update a GitHub issue.
- **Built the signal engine inside the database:** one PL/pgSQL function using window functions computes momentum,
  RSI, SMA-50/200, volatility, volume z-scores and 52-week breakouts for every stock. In CI it agrees with an
  independent pandas re-implementation on all 640 synthetic test rows, and hand-computed examples check the
  formulas. It is backed by a BCNF schema with CHECK and FK constraints, audit
  triggers and a least-privilege read-only role.
- **Cut query latency with measured index design** (`EXPLAIN ANALYZE` on a 259,600-row synthetic database sized like
  production; raw timings published):
  - paginated API requests: 0.09 ms against 27.6 ms without the primary key (about 300×);
  - sector aggregates: 233 ms → 0.19 ms through a materialized view;
  - a monitoring view rewritten with `LATERAL` lookups: about 30× faster (19.2 → 0.63 ms);
  - weekly benchmarks on production show 131–573× gains (medians of 7 runs) from the primary-key, partial and
    materialized-view designs.
- **Shipped a Streamlit dashboard and a read-only FastAPI service** on a shared data layer that runs queries in
  read-only transactions, with a demo mode and a one-command Docker Compose stack. It is covered by 423 automated
  tests in CI on Python 3.12 and 3.13 against a PostgreSQL 17 container.

**Optional fifth bullet (analytics roles):**
- **Backtested the momentum and sector-strength signals on 11.5 years of real production data** (Neon, May 2015 –
  Nov 2026). It had no look-ahead in decision timing, next-session execution and 11.2 bp NSE delivery costs. Momentum
  returned 9.6% a year and sector rotation 10.2%, against 12.5% for buy-and-hold (Sharpe 0.60 and 0.59 vs 0.89). I
  reported that neither signal beats the benchmark.

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
> 🔹 **Measure indexes, don't guess.** On a synthetic database the size of production, the composite primary key
> makes paginated API calls about 300× faster (0.09 ms vs 27.6 ms). A materialized view takes a sector report from
> 233 ms to 0.19 ms. Measuring also turned up a slow monitoring view that I rewrote: 19 ms → 0.63 ms.
>
> 🔹 **Let the database enforce the rules.** CHECK and FK constraints, audit triggers and a read-only role catch the
> impossible values and mistakes they encode, even when application code misses them.
>
> 🔹 **Be honest about results.** I backtested the momentum and sector-strength signals on 11.5 years of real data,
> with realistic trading costs. Both lost to simply holding all 25 stocks: 9.6% and 10.2% a year against 12.5%, with
> Sharpe ratios of 0.60 and 0.59 against 0.89. Not the
> exciting answer, but the true one.
>
> On top of the database there is a Streamlit dashboard, a read-only FastAPI service, a Docker Compose setup that runs
> everything with one command, and 423 automated tests.
>
> Code and write-ups: https://github.com/Bhavex/market-intelligence-pg
>
> #PostgreSQL #DataEngineering #Python #SQL #FinTech

## Where each number comes from

| Number | Source |
|---|---|
| 25 tickers, 5 sectors | `sql/02_seed.sql` |
| ~212,500 price rows and as many signal rows (production, 81 stocks) | `docs/BENCHMARKS.md`, table sizes measured on Neon (212,507 rows each, 1 Nov 2026); see `docs/CLAIMS.md` §2 |
| Three loads per trading day | `.github/workflows/daily_update.yml` schedule |
| 12 data-quality checks | `CHECKS` in `src/post_load.py` |
| Signal engine agrees with pandas on all 640 synthetic test rows | `tests/test_db_integration.py::test_refresh_signals_matches_the_independent_pandas_engine` (trend, anomaly, breakout, RSI and momentum); agreement shows consistency, not correctness of the definitions |
| ≈300×, 0.09 ms vs 27.6 ms; 233 → 0.19 ms; 19.2 → 0.63 ms | `docs/PERFORMANCE.md`; every individual run in `results/performance/production_scale_100_tickers.json` (local PostgreSQL 16, synthetic data, 100 tickers × 2,596 weekdays = 259,600 rows) |
| 131–573× on production | `docs/BENCHMARKS.md` (Neon, PostgreSQL 17, median of 7 runs: primary key 131.5×, partial index 169×, materialized view 573×). Individual runs were not kept for that date; `benchmark.py` now saves them to `results/benchmarks/latest.json` |
| 423 tests | `python -m pytest --collect-only -q` (339 run without a database) |
| 9.6% / 10.2% vs 12.5% CAGR, Sharpe 0.60 / 0.59 vs 0.89, 11.2 bp, May 2015 – Nov 2026 | **Real Neon data**: workflow run [41892962103](https://github.com/Bhavex/market-intelligence-pg/actions/runs/41892962103), `results/backtest/summary_neon_2015-05_2026-11.csv`, `docs/BACKTEST.md` |

The ≈300×, 233 ms and ≈30× figures come from synthetic data at production size on a local machine; the 131–573×
figures come from the real production database. Say which is which if an interviewer asks.

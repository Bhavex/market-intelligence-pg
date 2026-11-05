# Demo and viva defence: 30 likely questions

Short answers based on the actual code. File references let you open the proof on screen. Numbers come from
`docs/BENCHMARKS.md` (1 Nov 2026, Neon PostgreSQL 17); re-check them before the demo.

**Before the demo, have open:** `sql/01_schema.sql`, `sql/03_signals.sql`, `sql/12_integrity_and_reporting.sql`,
`src/daily_update.py`, `src/validation.py`, the latest green run of **Daily market update** and **Tests**, and a SQL
console with `demo/viva_queries.sql`.

---

## A. Database design and normalisation

**1. Walk me through the ER model.**
Strong entities: `sectors`, `cities` (with `states`) and `companies`. `daily_prices` is a weak entity of `companies`:
it is identified by `(ticker, price_date)` and has no meaning without its company. `signals` is 1:1 with a price row,
because its PK is also a composite FK to `daily_prices`. `fundamentals` is a dated weak entity of `companies`. The log
entities (`pipeline_runs`, `audit_log`, `rejected_prices`) use surrogate keys. Diagram: `README.md`, details in
`docs/SCHEMA.md`.

**2. What normal form is the schema in? Prove it.**
BCNF for every table. The one exception was found in the review: `cities` had `state → region`, a transitive
dependency, because `state` is not a key. Every other determinant is a key of its own table: `sector_id`, `ticker`,
`(ticker, price_date)`, `(ticker, snapshot_date)`. The step-by-step decomposition from a flat CSV (1NF → 2NF → 3NF →
BCNF) is in `docs/NORMALISATION.md` §3.

**3. You still have `region` in `cities`. Isn't that a 3NF violation?**
Yes, the column is redundant, but it is controlled redundancy. `states(state, region)` stores the fact once, and a
composite FK `cities(state, region) → states(state, region)` makes a contradictory region impossible, so no update
anomaly can occur. I kept the column so existing views and Power BI visuals would not break (`docs/MIGRATION_NOTES.md`).

**4. Why is `signals` a separate table if it has the same key as `daily_prices`?**
3NF does not require the split. It is a design choice: raw facts and derived values have different lifecycles.
Signals can be dropped and recomputed by `refresh_signals()` without touching source data. Wide reporting queries that
only need prices do not read signal columns. The FK with `ON DELETE CASCADE` guarantees there is never a signal without
its price.

**5. Natural key or surrogate key for `companies`?**
Natural: `ticker`, for the price tables, because every source (Yahoo, NSE files) uses it. A ticker is not a stable
identity, though, so `instruments` adds a surrogate `instrument_id`. `instrument_symbols` and `universe_membership`
record which symbol and which universe applied on which dates. Tata Motors keeps its history with an end date and
Hero MotoCorp gets a start date (`sql/18_instruments.sql`); `replace_company()` now maintains those intervals. Sectors and cities use `SMALLSERIAL` surrogates because their names are long and may be edited.

**6. Why don't you ever DELETE a company?**
Deleting would cascade into or orphan years of prices, and you would lose the history (a delete anomaly).
`is_active = FALSE` keeps the history queryable. The reporting views filter on it.

**7. Why `NUMERIC(14,4)` and not `FLOAT` for prices?**
`NUMERIC` is exact decimal. Floating-point would make `close = 1234.55` comparisons and the
`IS DISTINCT FROM` change detection unreliable (binary rounding), and money should not be approximated. Signals that are
ratios are rounded to 6 decimals on write.

## B. Constraints and integrity

**8. Which constraints protect the price table?**
- PK `(ticker, price_date)`, which also stops duplicates;
- FK to `companies`;
- `CHECK close > 0`, `CHECK high >= low`, `CHECK volume >= 0`;
- (added in migration 12) `open/high/low/adj_close > 0` and `price_date >= 1990`;
- a `source` CHECK;
- the `validate_price` BEFORE trigger, which blocks future dates and upper-cases tickers.

**9. How did you add constraints to a live table without breaking it?**
`ALTER TABLE … ADD CONSTRAINT … NOT VALID` is instant: it does not scan old rows, but every new or updated row is
checked from then on. `VALIDATE CONSTRAINT` then scans the old rows under a light lock (SHARE UPDATE EXCLUSIVE). If an old
row fails, the exception is caught and the constraint simply stays `NOT VALID` (`sql/12_integrity_and_reporting.sql`).
Tested: a deliberately bad row left setup working, and the constraint validated itself once the row was fixed.
That does not make the migration risk-free:
- `ADD CONSTRAINT` still needs a brief `ACCESS EXCLUSIVE` lock and waits behind long readers;
- only check and FK violations are caught, so any other error fails `setup_db.py`;
- a new or updated row that breaks a constraint still fails its statement.

**10. What is a deferrable constraint and why did you use one?**
`cities_state_region_fk` is `DEFERRABLE INITIALLY DEFERRED`, so it is checked at COMMIT, not per statement. `setup_db.py`
runs all SQL files in one transaction, and the seed file inserts cities before file 12 fills `states`. Checking at
commit lets both happen in one atomic step.

**11. Why can't the database enforce `low <= close <= high`?**
It could, but real Yahoo NSE bars sometimes have the close a few paise outside the high-low range (closing-auction
price). A hard CHECK would reject genuine data, so it is a warning in `validation.py` and `post_load.py`. The CHECKs only
encode rules that can never be legitimately broken.

## C. Indexing and performance

**12. Which indexes exist and why?**
- PK B-trees serve per-stock range scans and "latest row".
- `idx_daily_prices_date` serves cross-sectional "all stocks on day X".
- The partial indexes `idx_signals_anomaly` and `idx_signals_breakout` index only the rare TRUE rows.
- `idx_companies_sector`, `idx_pipeline_runs_job`, and (migration 12) the audit-key and failed-runs indexes.
- `ux_mv_sector_daily` is required for `REFRESH MATERIALIZED VIEW CONCURRENTLY`.

**13. Show me that an index actually helps.**
`docs/BENCHMARKS.md` is regenerated weekly by `src/benchmark.py` with `EXPLAIN ANALYZE`, median of 7 runs, inside
rolled-back transactions. One stock, 12 months: forced seq scan 15.9 ms vs PK index 0.12 ms (about 130×). Anomalies,
90 days: 41 ms vs 0.24 ms with the partial index (about 170×).

**14. What is a partial index and when is it worth it?**
An index with a `WHERE` clause, such as `ON signals(signal_date) WHERE is_anomaly`. It is worth it when queries always use
that predicate and it selects a small fraction of rows. The index is a fraction of the size, and updates to
non-matching rows do not touch it.

**15. Why didn't you add more indexes?**
Every index slows inserts and updates and uses space. `companies` has about 80 rows, where a sequential scan is
cheaper than any index. `(ticker, price_date DESC)` would duplicate the PK, which PostgreSQL can already scan backwards.
BRIN pays off at tens of millions of rows, not about 210k. Reasons are recorded in `docs/MIGRATION_NOTES.md`.

**16. Why a materialized view, and what is the trade-off?**
`v_sector_daily` aggregates the whole price and signal history on every query (132 ms). `mv_sector_daily` stores the
result (0.23 ms, about 570×). The trade-off is staleness: it is refreshed once after each load in `post_load.py`, with
`CONCURRENTLY` so readers are never blocked. That requires the unique index.

**17. Did you try partitioning?**
Yes, as a benchmark. A year-partitioned copy prunes a one-year query to 1 of 13 partitions (19 ms → 4.8 ms). I did not
partition the production table: at about 210k rows the gain is small, and partitioning complicates FKs from `signals`
and the PK. That is the honest conclusion of the measurement.

## D. Pipeline and SQL engine

**18. Explain the daily pipeline end to end.**
GitHub Actions at 17:10, 20:10 and 07:10 IST runs these steps:
1. `setup_db.py` applies the idempotent schema.
2. `daily_update.py` fetches the last 5 NSE sessions per stock, validates them (`validation.py`), upserts them, and calls
   `refresh_signals()`.
3. `post_load.py` refreshes the materialized view and runs 12 quality checks.
4. `verify_52w.py` cross-checks the 52-week signals against pandas.

Every job logs to `pipeline_runs`.

**19. What does "idempotent" mean here and how do you guarantee it?**
Running the same load twice leaves every price row unchanged; only run logs and `signals.computed_at` change. The
mechanism is
`INSERT … ON CONFLICT (ticker, price_date) DO UPDATE … WHERE (old values) IS DISTINCT FROM (new values)`. A re-run
updates nothing, writes nothing to the audit log and creates no new versions. The schema files use
`IF NOT EXISTS` / `CREATE OR REPLACE`, and CI applies them twice on every push to prove it.

**20. Why recompute signals in SQL instead of Python?**
The data never leaves the database: one set-based statement with window functions (`LAG`, `AVG … ROWS BETWEEN`,
`STDDEV_SAMP`) handles every stock at once and upserts the result. The logic lives in one place and every client (Power BI,
site, Excel) sees the same numbers. It also demonstrates PL/pgSQL. The Python replica in `fragility.py` is used only to
verify it. In CI the two agree on all 640 synthetic test rows for trend, anomaly, breakout, RSI and momentum. That
shows the two implementations are consistent, not that the definitions are right. Both were written from the same
specification, so a shared misunderstanding would agree too. The hand-computed examples in `tests/test_signals.py`
are the check on the formulas themselves.

**21. Why does the 52-week high exclude today?**
The window is `ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING`. If today were in the window, today could never exceed its
own maximum, so a breakout would be impossible. Values stay NULL until 252 prior sessions exist.

**22. What stops you from storing a half-day price as the close?**
`prices._session_finished()` holds back today's bar until 16:30 IST (15:30 close + 1 hour). `guard_final_sessions()` in
`upsert_rows()` refuses such a row on *any* code path. This came from a real incident: a 09:39 run stored intraday prices
on 13 Oct 2026 (`CHANGES.md`). The SQL function `expected_latest_trading_day()` uses the same 16:30 cut-off.

**23. How do you handle transactions and failures per stock?**
Each stock's upsert is its own transaction (`upsert_rows` commits). If one stock fails, the handler calls `rollback()`,
adds it to `tickers_failed` and moves on, so one bad symbol cannot block 24 good ones. The run is then `partial`. If more
than half fail, the job exits non-zero and the Action turns red. `pipeline_run()` records `failed` even on
`KeyboardInterrupt`/`SystemExit`.

## E. Data reliability

**24. How do you validate data before it goes in?**
`src/validation.py` runs before every insert in backfill, the daily update and the revision scan.
- **Rejected:** missing OHLC, zero or negative prices, negative volume, `high < low`, duplicate ticker+date.
- **Suspect:** a move beyond ±20% against the last unflagged session, judged only on data up to that row.
- **Warning only:** open/close outside the day's range, and gaps of 3+ NSE sessions.

Rejected rows are logged and saved in `rejected_prices` with the raw row as JSONB, instead of aborting the load.
Network or database errors still fail the ticker or the run. The old spike rule used the *next* session's price,
which made the cleaned history depend on the future. It is replaced by timestamped quality states in the append-only
`quality_states` table: a suspect row becomes rejected or accepted on a *later* run, and the earlier state stays
recorded (see `docs/DATA_LINEAGE.md`).

**25. Why is a spike only "suspect" at first, and rejected later?**
A one-day move that immediately reverses is the signature of a bad tick; a move that persists may be real news, like
results or a block deal. On the day the row arrives you cannot tell which, because the next session does not exist
yet. So the row is stored as `suspect`, and a backtest decision taken that day ignores it. On the next run, with the
next session in hand, the row becomes `rejected` (reversed) or `accepted` (persisted). Both states are kept with their
timestamps, so a replay of any past day sees only what was known then (`quality_as_of(ts)`). The check uses
`adj_close`, so splits and dividends do not look like jumps, and a suspect row is never used as the reference for the
next one, so one bad tick cannot flag the following days.

**26. What if Yahoo changes a price after you stored it?**
The next run's 5-session window overwrites it with Yahoo's latest value (only inside that window), and the weekly
`revision_scan.py` re-checks the full history. Neither can correct an error Yahoo keeps reporting. Every change is
kept in the bitemporal `price_versions` table (`sys_from`/`sys_to`, with the `run_id` that caused it) and classified in
`v_price_revisions` (dividend adjustment, intraday-as-close, hourly fill replaced, …).
`prices_as_of('2026-10-13 10:00+05:30')` returns the table as it was known at that moment, for any moment since
versioning began in Oct 2026. Earlier history is a one-time reconstruction from the audit log, not an observation.

**27. How do you know the pipeline didn't silently miss a day?**
`v_data_freshness` compares each stock's latest date with `expected_latest_trading_day()`, which reads the NSE holiday
calendar. `post_load.py` also checks calendar gaps over the last 30 days. The 07:10 IST run is strict: a missing day
fails the job and GitHub emails the owner. An empty Yahoo response now counts as a failure instead of a silent "0 rows".

**28. How is the system secured?**
The only secret is `DATABASE_URL`. It lives in a git-ignored `.env` locally and in a GitHub Actions secret in CI, and is
never printed. Analysts and Power BI log in as members of `readonly_access`, which can only `SELECT`
(least privilege: no INSERT, UPDATE, DELETE or DDL). Power BI is pointed at the reporting views. The CI test workflow uses its own throwaway PostgreSQL container and cannot reach production.

## F. Testing and limitations

**29. How did you test this without hitting the real database?**
The pytest suite fakes the psycopg connection and `yfinance.Ticker` (`tests/conftest.py`). It covers every validation
rule, the signal maths, upsert counting (inserted/updated/unchanged), insert-only fallback rows, the session guard, retries,
and the backfill and daily jobs end to end, with no network. SQL tests run against a disposable PostgreSQL 17 service in
GitHub Actions. They apply the schema twice, check every constraint and view, and compare `refresh_signals()` with the
pandas engine. Every test runs inside a rolled-back transaction.

**30. What are the main limitations, and what would you do next?**
- **Single unofficial source (Yahoo).** The research layer measures its agreement with official NSE/BSE closes. Next
  step: use the official bhavcopy as the primary source.
- **The holiday calendar covers 2026 only.** The 2027 list must be added in December.
- **End-of-day data only.**
- **Equal-weighted sectors**, because there are no free-float caps in the schema.
- **Fundamentals have no point-in-time history** before the project started.
- **Scale:** at millions of rows I would partition `daily_prices` by year (the benchmark shows the pruning) and switch the
  date index to BRIN.

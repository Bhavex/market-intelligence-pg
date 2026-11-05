# Interview prep: Sector-Wise Market Intelligence (PostgreSQL)

Likely internship interview questions about this repo, with short answers based on the code. Where the answer is
"it doesn't do that" or "that's a limitation", it says so. Being honest about limits usually lands better than
overselling.

## Database design

**1. Walk me through the schema.**
There are dimension tables (`sectors`, `states`, `cities`, `companies`) and fact tables (`daily_prices`, keyed on
`(ticker, price_date)`, and `signals`, a 1:1 derivation keyed on `(ticker, signal_date)`). Operational and audit
tables sit alongside them: `pipeline_runs`, `rejected_prices`, `audit_log` and `price_versions`. `sql/01_schema.sql`
is the core; the later numbered files add the calendar, point-in-time and reconciliation layers.

**2. Why a composite natural key `(ticker, price_date)` rather than a surrogate id?**
One row per stock per session is the actual business rule, so the PK enforces it. It also gives the idempotent
`ON CONFLICT (ticker, price_date)` upsert in `src/prices.py`, and its B-tree serves the API's "newest first" pagination
with a backward index scan (0.09 ms vs 27.6 ms with no index, in `docs/PERFORMANCE.md`). The cost is a wider key in
`signals` and on the FKs.

**3. You claim BCNF. Is there any redundancy?**
Yes, one deliberate case: `cities.region` duplicates `states.region`. It's kept consistent by a composite FK to
`states`, so the DB can't let them drift. `docs/NORMALISATION.md` has the steps. `signals` is technically derived
data, kept as a stored cache for read speed.

**4. How did you add constraints to a live table with existing data?**
`sql/12_integrity_and_reporting.sql` adds each CHECK/FK as `NOT VALID`, then tries `VALIDATE`. If old rows violate
it, it stays `NOT VALID` with a NOTICE, which means new and updated rows are still checked. This avoids a long lock
and a failed migration.

**5. What triggers exist and why?**
Audit triggers on `companies`/`fundamentals` and on price corrections/deletes (`sql/05`). There's a validate-price
trigger as a last line of defence, a versioning trigger on `daily_prices` that writes `price_versions` (`sql/08`),
and `forbid_mutation()` triggers that make `quality_states` and `fetch_log` append-only (`sql/14`, `sql/15`).

**6. What is the bitemporal table?**
`price_versions` stores every version of a stock-day with valid time (`price_date`) and transaction time
(`[sys_from, sys_to)`), and `prices_as_of(ts)` replays what the DB believed at a moment. It's SQL:2011-style system
versioning built by hand with an AFTER trigger, since PostgreSQL doesn't have it natively.

**7. How is access controlled?**
There's a `readonly_access` role for Power BI/Excel. The Streamlit app and FastAPI both go through
`marketintel/datasource.py`, which runs queries in `READ ONLY` transactions, and the API returns 405 for write
methods. Only GitHub Actions holds the `DATABASE_URL` write secret.

## Data engineering

**8. Describe the daily pipeline.**
GitHub Actions runs at 17:10, 20:10 and 07:10 IST. Each run fetches the last 5 NSE sessions from Yahoo
(`src/prices.py`), validates them (`src/validation.py`), upserts them, calls `refresh_signals()`, refreshes
`mv_sector_daily`, and runs the data-quality checks. On failure, `scripts/pipeline_alert.py` opens or updates a
GitHub issue.

**9. Why fetch 5 sessions and run three times a day?**
That's for resilience to late or revised Yahoo data and delayed cron. Re-fetching a window means a missed or bad day
gets repaired on the next run, and because the upsert is idempotent, re-running is safe. The 07:10 run is strict and
fails if any trading day is missing.

**10. How are signals computed?**
They're computed in the database by one PL/pgSQL function, `refresh_signals(p_from)` in `sql/03_signals.sql`. It
uses window functions (`LAG`, rolling `AVG`/`STDDEV_SAMP` with `ROWS BETWEEN`) over `PARTITION BY ticker ORDER BY
price_date`. It reads 400 calendar days of look-back so the 200-day SMA is correct, and SMA-200 is NULL until 200 rows
exist.

**11. How do you stop two runs from colliding?**
`src/run_lease.py` implements a database lease with a TTL and a heartbeat thread. Writes are fenced on the lease, and
`reap()` marks runs with no heartbeat as failed. Crash tests SIGKILL a load before, during and after commit, and check
that the recovered DB equals a clean run.

**12. Why a materialized view for sector stats?**
A plain view recomputed the aggregates on every read (233 ms). The MV is refreshed once per load and reads in
0.19 ms. The trade-off is staleness between refreshes, which is fine because the data only changes once a day.

## Data quality

**13. What does validation reject vs warn?**
It rejects missing OHLC, zero or negative prices, negative volume, and high < low. It warns (and keeps the row) when
open or close is outside the low–high range by more than a tolerance. Rejected rows go to `rejected_prices` with the
raw row as JSONB and the run id, so nothing disappears silently.

**14. What real data problems did you hit?**
These are from `docs/TRUST.md`: Yahoo left a daily candle empty for over a day; a 09:39 IST run stored mid-session
prices as the close for 24 of 25 stocks; and dividends rescale all earlier adjusted prices. The last one is why there's
a fetch ledger, append-only observations and a bitemporal history.

**15. How do you know a day is "missing" rather than a holiday?**
`sql/07_trading_calendar.sql` has an NSE holiday table and `is_trading_day()`. `v_session_coverage` labels every
expected ticker-session as present, holiday, suspended, not yet published, fetch failed or missing. Honest caveat: the
holiday table only covers 2026; for earlier years a holiday is *inferred* when no stock has a price.

**16. How do you check against an official source?**
`src/bhavcopy.py` and `src/reconcile.py` load NSE bhavcopy closes into `reference_prices`, and `reconciliation_results`
classifies disagreements (last price as close, stale value, split basis, intraday, unexplained).

## Testing

**17. How is this tested?**
There are 423 pytest tests run in CI (`.github/workflows/tests.yml`). Most are pure Python with no DB or network. The
SQL tests run against a throwaway PostgreSQL 17 service container and never touch production. The API has 34 tests
that use bundled synthetic data.

**18. How did you test the backtest accounting?**
`src/portfolio.py` is a share-and-cash ledger, checked against hand-calculated fixtures covering fees, splits,
bonuses, dividends and missing prices. It was also compared with the older weight-based engine: identical before
costs, and within 0.02 pp CAGR after costs.

**19. What isn't tested well?**
Yahoo's live behaviour can only be simulated or replayed. Real-data backtest numbers come from a workflow run on Neon,
not from CI. Performance numbers are from a local synthetic DB, not Neon.

## System design trade-offs

**20. Why compute signals in SQL rather than pandas?**
It keeps one source of truth next to the data, avoids moving about 200k rows out of the database, and means the API,
dashboard and Power BI all see identical numbers. The cost is that PL/pgSQL window logic is harder to unit-test and
debug than pandas, which is why the SQL tests exist.

**21. Why GitHub Actions + Neon instead of Airflow/cloud infra?**
It's free, it's a schedule plus secrets plus logs in one place, and the volume is small (EOD, 81 stocks). The
trade-offs are cron delays (hence three runs a day), no real orchestration or DAG UI, and dependence on GitHub
uptime.

**22. How would it scale to 2,000 stocks or intraday data?**
EOD for 2,000 stocks still fits on one Postgres instance. I'd partition `daily_prices` by date range, make signal
refresh incremental per ticker batch, and use `REFRESH MATERIALIZED VIEW CONCURRENTLY`. Intraday is a different
system: a time-series store (e.g. TimescaleDB), a streaming ingest and a paid feed. Yahoo wouldn't be appropriate.

## Research question

**23. What is the research question?**
"When free market data is wrong or revised, which research conclusions change?" (`docs/RESEARCH_PLAN.md`). The
conclusions under test are C1–C4: the backtest results, signal flip rates and sector rankings. Each is re-run on
alternative histories (first print, revised, official, quality-masked, dated universe, simulated errors) against
thresholds stated in advance.

**24. What did the backtest find?**
Neither signal beats the benchmark after 11.2 bp costs. Momentum top 5 makes 9.6% CAGR and sector rotation 10.2%,
against 12.5% for buy-and-hold (May 2015 – Nov 2026, Neon). Momentum loses even before costs. Rotation's 0.2 pp gross
edge is eaten by about 20× annual turnover. Only monthly equal-weight rebalancing won (14.0%, Sharpe 0.98).

**25. Is the backtest free of look-ahead bias?**
Only partly, and the docs say so. Decision timing is clean: signals use data up to the signal close and fills happen
at the next session's close. But the prices are today's revised Yahoo snapshot, and point-in-time capture only starts
at deployment. The universe is now dated (`universe_membership`, e.g. Tata Motors out and Hero MotoCorp in), but 25
current large caps still carry survivorship bias.

## The 5 hardest follow-ups

1. **"Your universe is 25 stocks that are large caps *today*. Isn't the whole backtest survivorship-biased?"**
   Agree, then scope it. Dated membership fixes the known swaps, but picking today's large caps still biases returns
   upward, and that applies to the benchmark too. The conclusion is relative ("the signals don't beat holding the same
   names"), so the bias affects both sides. I wouldn't claim anything about absolute returns. The fix is historical
   index constituents.

2. **"You ran many trials. How do you know 14% for equal-weight isn't a data-mined result?"**
   Every evaluation is logged in `backtest_trials`. `--rigour` reports a deflated Sharpe probability over all trials,
   95% block-bootstrap intervals, three cost levels and a held-out final 3 years with parameters chosen only on the
   earlier period. Also, equal-weight rebalancing has no parameters to tune. Still, 25 stocks over about 11 years is a
   small sample, so I'd call it suggestive.

3. **"Your RSI isn't Wilder's RSI, is it?"**
   Correct. `refresh_signals()` uses a 14-row *simple* average of gains and losses, not Wilder's exponential
   smoothing, so the values differ from TradingView and other charting tools. It's a consistent, documented definition
   inside the project, and SQL window frames make a simple average natural; Wilder's would need a recursive CTE or a
   loop. If I wanted values to match external tools, I'd switch definitions and re-run the fragility numbers.

4. **"Your performance numbers come from a synthetic local DB. Why should I believe them for Neon?"**
   The *ratios* and plan shapes (backward PK scan, `LATERAL … LIMIT 1` probes, partial index on `is_anomaly`) carry
   over, because they come from the access path, not the hardware. The absolute milliseconds don't, and Neon adds
   network and cold-storage latency. I'd re-run `src/perf_study.py` against a Neon branch before quoting absolute
   figures.

5. **"What happens if the lease holder is alive but paused (GC or network) past its TTL, and a second run starts?"**
   That's the classic fencing problem. The lease alone isn't enough, which is why writes are *fenced* on the lease:
   a writer whose lease was taken over fails its write instead of overwriting. The upsert is also idempotent, so the
   worst case is a duplicate re-write of the same values. The honest gaps: a heartbeat thread that can't connect only
   logs a warning, and the defaults for TTL and stale-after are tuning choices, not proofs. I'd point to the SIGKILL
   crash tests as evidence and to clock-independent fencing tokens as the formal fix.

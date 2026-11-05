# Daily update performance

The daily workflow (`.github/workflows/daily_update.yml`) takes about 13 minutes. This note records where that time goes and what was changed.

## How it was measured

Every stage now prints a `TIMING` line, and `daily_update.py` and `post_load.py` print a per-stage total at the end of the run:

```
TIMING daily_update:
TIMING   fetch_yahoo                   ...s  x100
TIMING   upsert_prices                 ...s  x100
...
```

`src/timing.py` holds the timer. Per-ticker stages (`fetch_yahoo`, `validate_screen`, `ledger_observations`, `upsert_prices`, `quality_assess`) are summed across tickers so the total for each stage is shown once. `setup_db.py`, `refresh_signals` and each `post_load` check print their own line.

What was measured in this session (local PostgreSQL 16, no access to the production database or secrets):

| Stage | Measured | Notes |
|---|---|---|
| `setup_db.py`, all 22 SQL files | 0.13 s of SQL, 0.4–0.7 s process | Local database. Against Neon each file is a round trip and some files rebuild views, functions and triggers every run |
| One `fetch_rows_ex` call | 9.4 s | Yahoo was not reachable from the sandbox, so this is the retry path: 3 attempts with `sleep(2)` + `sleep(4)` |

The production per-stage numbers are not in this document because the workflow secrets were not available here. The next scheduled run will print them in the Action log under the `TIMING` lines.

## Where the 13 minutes go

The loop in `daily_update.py` runs one ticker at a time, about 100 active tickers:

1. **Yahoo fetch (dominant).** Each ticker makes a daily-history request, plus an hourly request when there are gaps. A request takes 1–5 s on a GitHub runner, and a failed one retries with 2 s and 4 s sleeps. 100 tickers × ~5 s ≈ 8 minutes, all serial, with the CPU and database idle.
2. **Database round trips per ticker.** `screen`, `ledger.record_fetch`, `observations.record_fetched`, `upsert_rows` (fence, `set_config`, `executemany`, commit, twice) and `quality.assess_ticker` add up to about 12–15 round trips. At 30–80 ms to Neon that is about 1–1.5 minutes over 100 tickers.
3. **`refresh_signals`.** Recomputes signals from the earliest start date across tickers. It ran even when no price row changed.
4. **`setup_db.py`.** Re-ran every SQL file every run, recreating views, functions and triggers that had not changed.
5. **`official_prices.py` / `alt_feed.py`.** Network-bound, with a 1 s politeness sleep between exchange requests. Both are `continue-on-error`, so they were left alone.
6. **`post_load.py`.** One `REFRESH MATERIALIZED VIEW CONCURRENTLY` and 12 count queries. This is small next to the fetch loop.

## Changes

| Change | Effect | Results unchanged because |
|---|---|---|
| Yahoo fetches run in a thread pool (`FETCH_WORKERS`, default 8; `1` restores serial) before the database loop | The fetch stage drops from about N × t to about N × t / 8 | Fetches are network-only. The database loop still runs in ticker order, and an exception raised during a fetch is re-raised at the same point in the loop. `tests/test_pipeline_efficiency.py` checks that the parallel and serial runs give identical output |
| `schema_migrations` table: `setup_db.py` applies only new SQL files and stores a SHA-256 for each | Repeat runs skip all 22 files | A file that has already been applied is not re-run. If an applied file's checksum changes, the run stops with an error, so an edited migration can't be skipped silently |
| `refresh_signals` is skipped when no price row was inserted or updated and no price row since the start date lacks a signal | Saves the signal recompute on retry runs and no-change runs | If prices were written by an earlier run that crashed before signals were computed, the missing-signal check forces the refresh |

The heal window (the last 5 sessions are re-fetched every run) is deliberate, because it lets Yahoo's late corrections overwrite earlier values. It was not narrowed.

## Schema migrations

`setup_db.py` keeps a `schema_migrations` table with `(filename, checksum, applied_at)`. On the first run against an existing database the table is empty, so every file is applied once more (all files are idempotent) and recorded. After that, only new files run. If an applied file is edited, the run fails and names the file. Put changes, including next year's NSE holidays, in a new numbered SQL file.

# Repository audit

Audit date: 4 Nov 2026. Branch `claude/improvements`. Scope: every file under `src/`, `sql/`, `tests/`,
`.github/workflows/`, `demo/` and `docs/`. I read the `results/` and `site/` folders only to see how they
connect to the pipeline.

**How I checked:** I read every file. I ran the existing test suite (52 pass, 7 skipped without a database). I applied
the full schema (`src/setup_db.py`) to a **local throwaway PostgreSQL 16** and ran the database tests against it. I did
not connect to the production Neon database.

Severity: **Critical** = wrong data or a silent pipeline failure is likely · **High** = a real risk you must manage ·
**Medium** = a weak spot that will cost you in a demo or at scale · **Low** = polish.

## Summary

| # | Severity | Finding | Status |
|---|---|---|---|
| 1 | Critical | Backfill writes hourly-fallback rows as if they were authoritative daily candles | **Fixed** |
| 2 | Critical | No validation before insert: bad Yahoo bars reach `daily_prices` or crash a whole ticker | **Fixed** (task 2) |
| 3 | High | NSE holiday calendar covers 2026 only; freshness/gap checks will misfire from 1 Jan 2027 | Open (needs the official 2027 list) |
| 4 | High | An empty Yahoo response counts as a *successful* daily load | **Fixed** |
| 5 | High | `pipeline_run()` crashes with `UnboundLocalError` on `SystemExit`/`KeyboardInterrupt`, hiding the real error and leaving the run row as `running` | **Fixed** |
| 6 | High | Tests do not run in CI; DB tests need a manually prepared database | **Fixed** (tasks 3–4) |
| 7 | Medium | `cities.state → region` is a transitive dependency; `docs/NORMALISATION.md` claims BCNF | **Fixed** (migration adds `states` + composite FK) |
| 8 | Medium | Missing CHECK constraints on `open/high/low/adj_close`, RSI range, volatility sign, pipeline timestamps | **Fixed** (migration, `NOT VALID` then validated) |
| 9 | Medium | `price_versions.ticker` has no foreign key | **Fixed** (migration) |
| 10 | Medium | `daily_update` used the runner's UTC date, not the IST date | **Fixed** |
| 11 | Medium | `v_latest_snapshot` includes inactive (replaced) tickers with stale dates | **Fixed** (new `v_latest_prices_by_sector` filters them out) |
| 12 | Medium | FK `daily_prices.ticker` has `ON UPDATE CASCADE` but `signals → daily_prices` does not, so a ticker rename is blocked halfway | Documented (renames go through `replace_company()`) |
| 13 | Medium | `fundamentals.dividend_yield` units changed upstream (yfinance ≥0.2.5x returns percent, older versions a fraction) | Documented |
| 14 | Medium | `requirements.txt` has lower bounds only; a breaking pandas/yfinance release can break the scheduled job | Documented |
| 15 | Low | Dead helper `prices.fetch_rows()` (the buggy path from #1) | **Removed** |
| 16 | Low | NumPy deprecation in `fragility.py` (`np.datetime64("NaT")` without a unit): a hard error in a future NumPy | **Fixed** |
| 17 | Low | `test_trust.PointInTime.test_no_change_reload_creates_no_version` fails on an empty test database | **Fixed** (test seeds its own row) |
| 18 | Low | `v_sector_daily` joins `signals` a second time to reach the 52-week columns | Documented (needed to keep the view's column order stable for Power BI) |
| 19 | Low | `benchmark.yml` runs `git push` even when there is nothing to commit (`a || b && c`) | Documented (harmless; workflow left untouched) |

## Details

### 1. Backfill stored hourly-fallback rows as authoritative (Critical, fixed)
`prices.fetch_rows()` merged Yahoo's daily candles **and** the rows rebuilt from hourly bars into one list.
`backfill.py` then passed it to `upsert_rows()` in full-upsert mode, so:
- approximate hourly rows (volume 20–50% low, OHL slightly off) could **overwrite** a correct stored row;
- they were labelled `source = 'yahoo_daily'`, so `price_versions`, `v_price_revisions` (`hourly_fill_replaced`) and
  the trust score could not tell them apart.

`daily_update.py` and `revision_scan.py` already handled this correctly (fallback rows are `insert_only`).
**Fix:** `backfill.py` now uses `fetch_rows_ex()` and upserts the two sets separately, the same way the daily job
does. `rows_upserted` now counts rows actually written, as in the daily job. The unused `fetch_rows()` was removed.

### 2. No pre-insert validation (Critical, fixed in task 2)
The only filter was `close is None or close <= 0`. A Yahoo bar with `high < low`, a zero open or a duplicate date
reached the database. There it either broke the whole ticker's `executemany` on the existing `CHECK (high >= low)`
(all of that ticker's rows rolled back, ticker marked failed), or was stored and only reported later by
`post_load.py`. See `src/validation.py`.

### 3. Holiday calendar covers 2026 only (High, open)
`market_holidays` lists the 2026 NSE holidays only. From 1 Jan 2027, `is_trading_day()` will treat every 2027
holiday as a trading day. The 07:10 IST strict run will then fail `freshness` / `calendar_gaps_30d` on the first holiday
(Republic Day, 26 Jan 2027). Earlier years are missing too, so calendar-based gap checks are only exact for 2026.
**Action:** add the 2027 list to `sql/07_trading_calendar.sql` when NSE publishes it (usually mid-December). I did not
guess dates. The new validation module's gap check is calendar-aware but only warns, so it cannot fail on a missing
holiday.

### 4. An empty Yahoo response was a "success" (High, fixed)
If `yf.Ticker().history()` returned an empty frame (rate limit, delisted symbol, Yahoo outage), the ticker printed
`inserted 0, updated 0` and did not count as failed. The "too many failures" guard could never fire for a total outage.
Only the next-morning strict freshness check would notice. The heal window always spans 5 *finished* trading sessions,
so a healthy response is never empty. **Fix:** no daily rows, no fallback rows and nothing held back → the ticker is
recorded as failed.

### 5. `pipeline_run()` and non-`Exception` exits (High, fixed)
`status` was only assigned in the `try` success path and in `except Exception`. A `SystemExit`, `KeyboardInterrupt` or
`GeneratorExit` inside the block reached `finally` with `status` unbound. That raised `UnboundLocalError`, masked the
original error and left the `pipeline_runs` row as `running` forever, which `v_pipeline_health` would show.
**Fix:** `status` defaults to `failed` and the handler catches `BaseException` (and still re-raises).

### 6. Tests not in CI (High, fixed)
There was no test workflow. The DB tests ran only when someone set `TEST_DATABASE_URL` by hand. Added a mocked pytest
suite and `.github/workflows/tests.yml`.

### 7. `state → region` transitive dependency (Medium, fixed)
`cities(city_id, city_name, state, region, …)`: `region` depends on `state`, not on the key, so `cities` is in 2NF but not
3NF/BCNF. `docs/NORMALISATION.md` said every table was BCNF. With nine cities the anomaly is theoretical, but an examiner
can spot it. **Fix (non-breaking):** the migration adds `states(state PK, region)` and a composite FK
`cities(state, region) → states(state, region)`. A city can then never carry a region that contradicts its state, and the
existing views and Power BI fields keep working. The trade-off is explained in `docs/MIGRATION_NOTES.md`.

### 8–9. Missing constraints and FK (Medium, fixed)
`CHECK (close > 0)` existed, but nothing stopped a zero `open`/`high`/`low`, a zero `adj_close`, an RSI outside 0–100 or a
negative volatility. `price_versions.ticker` had no FK. The migration adds them as `NOT VALID` (instant, no full scan, never
fails on existing rows) and then tries `VALIDATE CONSTRAINT`, so they become fully validated wherever the existing data
is already clean.

### 10. UTC vs IST date (Medium, fixed)
`daily_update.py` started its signal-refresh window at `date.today()`, the runner's UTC date. It now uses `prices._now()`
(IST), the clock every other part of the pipeline uses.

### 11. Inactive tickers in the latest snapshot (Medium, fixed)
`v_latest_snapshot` takes the latest row per ticker from `v_company_daily`, which filters on `in_dashboard` but not
`is_active`. A ticker replaced through `replace_company()` stays on the snapshot with an old date. The view is left
unchanged so Power BI keeps working; the new `v_latest_prices_by_sector` returns active stocks only.

### 12. Inconsistent `ON UPDATE CASCADE` (Medium, documented)
Updating `companies.ticker` cascades into `daily_prices`, but `signals` has a plain FK to `daily_prices`, so the cascade
fails. The project never renames tickers in place (`replace_company()` deactivates the old one and inserts a new one).
Rebuilding the FK would rewrite constraints on the largest table for a path that is never used, so I left it. If a rename
is ever needed: delete the signals, rename, then call `refresh_signals()`.

### 13. Dividend-yield units (Medium, documented)
In 2025 Yahoo changed `dividendYield` from a fraction (0.015) to a percent (1.5). Snapshots before and after the change
are not comparable. Normalise in Power BI, or divide by 100 when the value is > 1.

### 14. Unpinned dependencies (Medium, documented)
`yfinance>=0.2.54` and `pandas>=2.2` have no upper bound. The tests pass with pandas 3.0. CI now runs on every push, so a
breaking release shows up in CI before the scheduled run hits it. Pinning exact versions would stop the
yfinance fixes Yahoo changes often require, so I did not pin.

## Things that are already good (worth saying in the demo)
- Idempotent loads (`ON CONFLICT … DO UPDATE … WHERE … IS DISTINCT FROM`); only changed rows are written.
- A guard (`guard_final_sessions`) that makes it impossible to store an unfinished session as a close.
- A 5-session self-healing window driven by the trading calendar, not calendar days.
- Bitemporal price history with an exclusion constraint, and an audit trigger that logs only real changes.
- A data-quality gate that turns the Action red, and the least-privilege `readonly_access` role.
- No secrets in the repo: `DATABASE_URL` comes only from `.env` (git-ignored) or the Actions secret.

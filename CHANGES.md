# CHANGES: 52-week breakout signals, sector breakout counts, and a price-data fix

Branch `feat/52w-signals-and-data-quality` (not merged, not pushed). Written 13 Oct 2026, 23:30 IST.

> **Read this first: nothing here has been run on the production Neon database yet.**
> `DATABASE_URL` is only stored as a GitHub Actions secret and was not available where this work was done. Everything
> was built and tested on a **local PostgreSQL replica** with the same schema and the same 72,725 price rows
> (loaded from Yahoo with the repo's own `backfill.py`). The MARUTI / data-quality findings below come from
> **GitHub Actions logs, Yahoo, NSE's official bhavcopy files and the published dashboard export** (which is generated
> from production), not from direct queries. The exact steps to apply and re-verify on production are in section 7.

## 1. Summary for Adya

- **Your 52-week breakout signal is built** (`high_52w`, `breakout_52w`, `pct_from_52w_high`, `strong_breakout`) and the
  sector breakout count is in `v_sector_daily` (`breakout_count`, `strong_breakout_count`). Independently recomputed
  in pandas for 3 stocks: exact match.
- **MARUTI was a real data error, and it is bigger than MARUTI.** The 13 Oct rows in production were written by a run at
  **09:39 IST (market open since 09:15)**, so they hold *intraday* prices, not closing prices. This affects **24 of 25
  tickers**; 16 are more than 0.5% away from NSE's official close (HCLTECH -4.64%, TECHM -3.05%, TCS -2.89%, INFY -2.83%).
- **12 Oct was a normal trading day** (NSE published a bhavcopy for it). Yahoo's *daily* series has no 12 Oct bar for any of
  the 25 stocks, so those rows came from hourly bars. All 25 **closes are exactly right** (25/25 match NSE), but open/high/low
  are slightly off and the **volume is 20-50% too low** (mean 21.6%).
- **Several numbers quoted in the report move once 13 Oct is corrected** (section 6): Pharma +5.0% -> +5.4% YTD, IT -24.3% -> -21.9%,
  backtest finals 461 -> 463 and 416 -> 420. Rankings do not change. Volatility and correlation tables move by 0.05 pt / 0.001 or less.

## 2. What changed

**New columns in `signals`** (`sql/01_schema.sql`, `ADD COLUMN IF NOT EXISTS`, safe to re-run) and their logic inside
`refresh_signals()` (`sql/03_signals.sql`):

| Column | Definition |
|---|---|
| `high_52w` NUMERIC(12,2) | max `adj_close` over the **previous 252 trading days, today excluded** (`ROWS BETWEEN 252 PRECEDING AND 1 PRECEDING`) |
| `breakout_52w` BOOLEAN | `adj_close > high_52w` |
| `pct_from_52w_high` NUMERIC(8,2) | `ROUND((adj_close / high_52w - 1) * 100, 2)`: **negative below the high, positive on a breakout day** |
| `strong_breakout` BOOLEAN | `breakout_52w AND volume_zscore_20d > 2` (NULL z-score = FALSE) |

All four are NULL unless the row has exactly 252 prior non-null `adj_close` rows, so the first 252 rows of each stock are NULL.
The high excludes today because otherwise today's price could never beat its own maximum and a breakout would be impossible.
They are recomputed over the **full history of every ticker on every run** (Yahoo restates old `adj_close` after dividends), only
changed rows are written, and `strong_breakout` is computed after `volume_zscore_20d` in the same call.

**`v_sector_daily`**: `breakout_count` and `strong_breakout_count` appended at the end with `COUNT(*) FILTER (WHERE ...)`,
via `CREATE OR REPLACE VIEW` (no DROP). Proven on the replica: the original 10 columns are identical in name, order and type;
the view keeps the same object id and the same grants; `v_sector_strength` works; `powerbi_reader` can read everything.
`mv_sector_daily` (the materialized copy used by the site) still has its old 10 columns, because a materialized view cannot be altered in place.

**Pipeline** (`src/daily_update.py`, `src/prices.py`):
- re-upserts the **last 5 NSE trading sessions** for every ticker on every run (it used 5 *calendar* days, about 3 sessions), with `ON CONFLICT ... DO UPDATE`;
- never stores today's bar before **16:30 IST** (15:30 close + 1 h buffer; the old guard was 16:00 and lived only in `prices.py`);
- logs rows **inserted / updated / unchanged / held back** per ticker and in total; unchanged rows are no longer rewritten;
- rows rebuilt from hourly bars are now **insert-only**: with a 5-session re-upsert they could otherwise have overwritten a good official row whenever Yahoo's daily feed had a gap.

Also added: `src/verify_52w.py` + `sql/validation/52w_checks.sql` (also a new step in the daily workflow), `src/dq_audit.py`,
`src/adya_snapshot.py`, `tests/test_pipeline.py`, README updates.

## 3. Verification of the 52-week signal (replica)

- **10/10 SQL checks pass** (all-or-nothing NULLs, first 252 rows NULL, no extra NULLs, sign/definition of breakout and strong, `high_52w` = prior-252 max re-derived by a self-join, view totals = per-stock flags for every day and sector, original view columns intact, reader grants).
- **Independent pandas + Decimal recomputation** from `daily_prices` for ICICIBANK.NS, MARUTI.NS, TCS.NS: **0 mismatches** in all four columns over 2,909 rows each (2,657 valued rows; 177 / 153 / 132 breakout days).
- **252 leading NULL rows for 25/25 tickers.** No ticker has a missing `adj_close`, so there are no exceptions. A deliberate test (setting one `adj_close` to NULL) correctly blanked that row and the next 252 (505 NULLs), and pandas agreed.
- The checks can fail: a corrupted `high_52w` was caught by two independent checks.
- Idempotent: a second `refresh_signals` run changed 0 rows; a full-history run takes about 5 s locally.

Breakout stock-days per sector per year (replica; a stock-day counts when `adj_close` > its prior 252-day high):

| Year | Auto | Banking | FMCG | IT | Pharma | Total |
|---|---|---|---|---|---|---|
| 2016 | 97 | 55 | 31 | 13 | 7 | 203 |
| 2017 | 92 | 127 | 113 | 46 | 6 | 384 |
| 2018 | 25 | 71 | 142 | 129 | 47 | 414 |
| 2019 | 11 | 112 | 55 | 62 | 32 | 272 |
| 2020 | 45 | 21 | 53 | 122 | 138 | 379 |
| 2021 | 41 | 88 | 64 | 193 | 90 | 476 |
| 2022 | 64 | 51 | 62 | 9 | 45 | 231 |
| 2023 | 115 | 52 | 95 | 66 | 112 | 440 |
| 2024 | 157 | 75 | 71 | 129 | 188 | 620 |
| 2025 | 93 | 74 | 2 | 9 | 14 | 192 |
| 2026 (to 13 Oct) | 35 | 31 | 18 | 4 | 53 | 141 |

(2015 has none by construction: the first 252 days are NULL.) Five most recent breakouts: DIVISLAB 23 Sep 2026 (+0.51%), DIVISLAB 8 Sep (+1.14%, strong), BAJAJ-AUTO 1 Sep (+1.90%, strong), DIVISLAB 31 Aug (+2.47%), BAJAJ-AUTO 31 Aug (+1.70%, strong).
No sector had a breakout on 13 Oct.

## 4. MARUTI.NS findings

| | 12 Oct | 13 Oct |
|---|---|---|
| Database / dashboard export (production) | close 11,315 | close **11,320** |
| NSE bhavcopy (official) | O 11,420 H 11,519 L 11,275 **C 11,315**, vol 323,997 | O 11,315 H 11,349 L 11,125 **C 11,245**, vol 761,548 |
| Yahoo daily (pulled 23:05 IST) | **no bar** | O 11,315 H 11,349 L 11,125 C 11,245, vol 761,480 |
| Yahoo hourly bars | O 11,437 H 11,510 L 11,275 C 11,315, vol 268,006 | last bar 11,245 |

- **13 Oct (11,320) is an incomplete same-day price, confirmed by two independent sources** (Yahoo and NSE both say 11,245; Adya's number was right).
  GitHub Actions log: run `38345084319` started **04:09 UTC = 09:39 IST**, logged `MARUTI.NS: 6 rows` at 09:40 IST, i.e. about 6 hours before the 15:30 close.
  That run used the code at commit `9e335f5`, which filled "today" from Yahoo's live quote. The next run (09:59 IST, commit `256aafb`) added a guard that *skips* today but does not remove the earlier row.
  The dashboard export shows the same 11,320 and a 1-day return of +0.04% (11,320 / 11,315), consistent with the database.
- **12 Oct (11,315) is the correct close** (matches NSE), even though Yahoo has no daily bar for that date. The row was built at 09:59 IST from hourly bars. Its open (11,437 vs official 11,420), high (11,510 vs 11,519) and volume (268,006 vs 323,997, -17%) are inexact.
- `daily_prices.loaded_at` exists and records when each row was last written; I could not query it on production (see section 7 for the query). The Actions logs above give the same answer.
- The earlier scheduled run (01:52 IST on 13 Oct, `38303771389`) also saw no 12 Oct bar from Yahoo, so the Yahoo gap predates the manual runs.

**All 25 tickers, last 10 sessions (29 Sep - 13 Oct), production closes from the dashboard export vs Yahoo and NSE:**

| Flag | Result |
|---|---|
| Price difference > 0.5% | **16 rows, all on 13 Oct** (none on any earlier day). 12 Oct: 25/25 exact vs NSE. |
| Rows on weekends / NSE holidays | 0 |
| Missing trading days | 0 (2 Oct holiday correctly absent) |
| Identical consecutive closes | 1: SUNPHARMA.NS 12 Oct = 1,760 = 9 Oct. **Not an error**: NSE's official 12 Oct close is also 1,760. |
| Trading days where Yahoo daily has no bar | 25 (all 12 Oct), see above |

Limits of this audit: it compares **close** only (the export has no open/high/low/volume/adj_close) and reflects production as of 04:31 UTC.
`src/dq_audit.py` does the full check (close, adj_close, volume, `loaded_at`) directly on the database.

## 5. Corrected and UNVERIFIED rows

**No production row has been changed by me.** Status of every affected row:

| Rows | Problem | Official value known? | Status |
|---|---|---|---|
| 13 Oct, 24 tickers (table below; HDFCBANK matches) | intraday price stored as close | Yes: NSE close, and Yahoo agrees on the close (checked for all 25 tickers) | **Will be corrected by the pipeline's upsert** on the first run after 16:30 IST with the new code (or the old code, whose 5-calendar-day window also covers 13 Oct). Verified on the replica: 25 rows updated, audit_log keeps old and new values, then re-run = 0 changes. **Not yet done on production.** |
| 12 Oct, all 25 tickers | open/high/low approximate; volume 20-50% low (mean 21.6%, max 52%); **close exact** | Yes, in NSE bhavcopy, but Yahoo daily will not supply it | **Left as is** (not a same-day bar, and no hand-edits). Needs a decision, section 8. Affects `volume_zscore_20d` on 12 and 13 Oct and so `strong_breakout` / anomaly flags. Stated from the replica's rebuild; in production only the close is confirmed. |
| 13 Oct, HDFCBANK | none | NSE 714.00 = stored 714.00 | OK |
| **UNVERIFIED** | none. Every value that could be compared has an official value. Not verified: the adj_close and volume columns on production for dates before 12 Oct (the export has no such columns). | | run `src/dq_audit.py` on production |

13 Oct, stored vs official:

| Ticker | Stored in production (intraday, 09:39 IST) | NSE official close | Difference |
|---|---|---|---|
| HCLTECH.NS | 1,264.50 | 1,326.00 | -4.64% **>0.5%** |
| TECHM.NS | 1,496.50 | 1,543.50 | -3.05% **>0.5%** |
| TCS.NS | 2,203.70 | 2,269.30 | -2.89% **>0.5%** |
| INFY.NS | 1,032.05 | 1,062.10 | -2.83% **>0.5%** |
| WIPRO.NS | 165.65 | 169.00 | -1.98% **>0.5%** |
| HINDUNILVR.NS | 1,850.60 | 1,875.50 | -1.33% **>0.5%** |
| NESTLEIND.NS | 1,351.30 | 1,334.00 | +1.30% **>0.5%** |
| BAJAJ-AUTO.NS | 10,192.00 | 10,318.00 | -1.22% **>0.5%** |
| ICICIBANK.NS | 1,345.70 | 1,360.20 | -1.07% **>0.5%** |
| M&M.NS | 2,833.80 | 2,864.40 | -1.07% **>0.5%** |
| SBIN.NS | 948.00 | 957.00 | -0.94% **>0.5%** |
| AXISBANK.NS | 1,256.60 | 1,268.40 | -0.93% **>0.5%** |
| CIPLA.NS | 1,319.70 | 1,329.60 | -0.74% **>0.5%** |
| MARUTI.NS | 11,320.00 | 11,245.00 | +0.67% **>0.5%** |
| DRREDDY.NS | 1,232.00 | 1,240.00 | -0.65% **>0.5%** |
| ITC.NS | 259.40 | 261.00 | -0.61% **>0.5%** |
| DABUR.NS | 377.00 | 378.75 | -0.46% |
| HEROMOTOCO.NS | 4,976.00 | 4,997.50 | -0.43% |
| KOTAKBANK.NS | 440.70 | 439.00 | +0.39% |
| EICHERMOT.NS | 7,136.00 | 7,163.00 | -0.38% |
| BRITANNIA.NS | 4,812.10 | 4,801.30 | +0.22% |
| LUPIN.NS | 1,949.60 | 1,953.60 | -0.20% |
| SUNPHARMA.NS | 1,767.50 | 1,770.70 | -0.18% |
| DIVISLAB.NS | 9,297.50 | 9,285.00 | +0.13% |
| HDFCBANK.NS | 714.00 | 714.00 | +0.00% |

## 6. Before / after of the numbers quoted in the report

Method: Adya's queries 3a, 3b, 3c and 5 saved before and after, with `src/adya_snapshot.py`. Full details in `docs/regression_check_2026-10-13.md`.

- **Code changes alone (52-week columns, new view columns, pipeline rewrite), same data: all four results identical** (60 + 5 + 5 + 141 rows).
- **Correcting the stale 13 Oct rows moves these numbers.** The replica was put into production's stale state using the dashboard export's closes; that reproduces the report to the digit (Pharma +5.04%, IT -24.28%, volatility 22.33 / 21.10, correlation 0.348, finals 461.45 and 415.91), which is why the deltas can be trusted.

| Report item | Report (stale 13 Oct) | Corrected | Change |
|---|---|---|---|
| 2026 YTD Pharma (strongest) | +5.04% | +5.39% | +0.35 pt |
| 2026 YTD Banking | -5.44% | -4.95% | +0.49 pt |
| 2026 YTD Auto | -11.63% | -11.19% | +0.44 pt |
| 2026 YTD FMCG | -17.88% | -17.73% | +0.15 pt |
| 2026 YTD IT (weakest) | -24.28% | -21.87% | +2.41 pt |
| Annual volatility Banking / IT | 22.33% / 21.10% | 22.34% / 21.15% | +0.01 / +0.05 pt (IT rounds 21.1 -> 21.2) |
| Correlation IT-FMCG | 0.348 | 0.349 | +0.001 |
| Backtest final: strategy | Rs 461.45 | Rs 462.98 | +1.53 |
| Backtest final: equal-weight | Rs 415.91 | Rs 419.91 | +4.00 |
| Oct 2026 month: strategy / equal-weight | -1.71% / -0.14% | -1.38% / +0.82% | +0.33 / +0.96 pt |

Unchanged: every other row of 3a, all other volatility and correlation cells, all months of the backtest before Oct 2026, and every ranking.
**The last backtest month (Oct 2026) is only 13 days long and ends on the 13 Oct row**, so the final values depend on one day. The strategy trails equal-weight in that month either way, but the gap widens from 1.57 pt (-1.71% vs -0.14%) to 2.20 pt (-1.38% vs +0.82%).
Absolute levels on the replica differ slightly from production in older years because Yahoo restates adj_close; only the deltas above carry over.

## 7. To apply and verify on production (in this order)

```bash
export DATABASE_URL=...   # production, from your .env / secret; never commit it
python src/adya_snapshot.py --tag before          # 1. read-only, BEFORE anything changes -> /tmp/before_*.csv
python src/dq_audit.py                            # 2. read-only, predicted from the export: 16 PRICE_DIFF + 25 LOADED_BEFORE_CLOSE, all on 13 Oct
# 3. merge the branch (or run the workflow from it), then after 16:30 IST run "Daily market update"
python src/dq_audit.py                            # 4. expected: PASS
python src/verify_52w.py                          # 5. expected: ALL CHECKS PASSED
python src/adya_snapshot.py --tag after && python src/adya_snapshot.py --compare before after
```
Query for when each row was written: `SELECT ticker, price_date, close, loaded_at AT TIME ZONE 'Asia/Kolkata' FROM daily_prices WHERE price_date >= '2026-10-09' ORDER BY ticker, price_date;` (and `audit_log` shows each correction with old and new values).
The first run after the merge computes the 52-week columns for ~66k rows (about 5 s locally). `docs/BENCHMARKS.md` will show a longer `refresh_signals` time at the next weekly benchmark because it now includes the full-history step.

## 8. Decisions and risks

1. **Scheduled runs are not reliable.** Only one scheduled run has ever fired (01:52 IST on 13 Oct); none of the 17:10 / 20:10 IST slots fired on 12 or 13 Oct, and nothing has run since 09:59 IST. So the stale rows are still in production and on the public dashboard. A manual "Run workflow" after 16:30 IST fixes them.
2. **12 Oct volume/OHLC**: the hourly fallback is only approximate. Using NSE's bhavcopy as the gap source (it is available and has the official OHLC and volume) would fix it properly; I did not add a second data source without your say-so.
3. **`mv_sector_daily`** does not include the new columns; rebuilding it needs DROP + CREATE + re-grant. Left as is.
4. `pipeline_runs.rows_upserted` now means rows *actually written* (it used to count rows submitted); the breakdown is in `message`.

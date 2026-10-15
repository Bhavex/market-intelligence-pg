# How much can you trust free market data?

*A point-in-time database for NSE end-of-day prices, a reconciliation against the exchanges'
official files, and a measurement of how often data errors flip trading signals.*

Live results: the **"Can you trust free market data?"** section of the dashboard, and the Markdown
report printed by `python src/trust_report.py` (also attached to every run of the *Data trust study*
workflow).

---

## 1. The problem

Free end-of-day data (here Yahoo Finance) is not a fixed record. During the first week of this
project the pipeline itself ran into three different kinds of after-the-fact change:

| Date | What happened | Effect if nobody notices |
|---|---|---|
| 12 Oct 2026 | Yahoo left Monday's daily candle empty for over a day | a trading day silently missing |
| 13 Oct 2026 | a run at 09:39 IST stored mid-session prices as the close for 24 of 25 stocks (Maruti: ₹11,320 stored vs ₹11,245 official) | wrong signals on the dashboard for a day |
| every dividend | Yahoo rescales all earlier adjusted prices | yesterday's stored history no longer matches today's download |

Most dashboards and backtests overwrite old values. They therefore cannot answer
*"what did the system believe at that moment?"*, cannot measure how often the data changes,
and can contain look-ahead bias: a historical decision is evaluated on values that were only
known later.

**Research questions**
1. How often, how much, and in which ways is free NSE end-of-day data revised after first publication?
2. How closely does it agree with the exchanges' official closing prices, at first print and after it settles?
3. How fragile are common technical signals to these errors, i.e. how often do they give a different answer?
4. Can a relational database make every stored price auditable, replayable and scored for trust?

## 2. Design (what was built)

### 2.1 Bitemporal price history — `sql/08_point_in_time.sql`
* `price_versions` keeps **every** version of every stock-day with two time axes:
  *valid time* = `price_date` (the session the fact describes) and *transaction time* =
  `[sys_from, sys_to)` (when the database held that version). This is the SQL:2011 notion of a
  system-versioned (bitemporal) table, implemented with an AFTER trigger on `daily_prices`.
* **Temporal integrity:** an exclusion constraint
  `EXCLUDE USING gist (ticker WITH =, price_date WITH =, tstzrange(sys_from, sys_to) WITH &&)`
  makes overlapping versions of the same stock-day impossible; a partial unique index allows only
  one current version.
* **Provenance:** every version records the pipeline run that produced it (`run_id`, passed from
  Python through the session setting `app.run_id`) and the source of the value
  (`yahoo_daily` candle or `yahoo_hourly` rebuild).
* **Edge cases handled in the trigger:** two changes in one transaction collapse into one
  version; a row inserted and deleted in one transaction leaves no trace; a reload with identical
  values creates no version (the trigger has a `WHEN … IS DISTINCT FROM` condition).
* **History recovered, not just future:** on first install the table is seeded from the existing
  audit log, so the 12–13 Oct incidents that happened *before* this feature existed are replayable.
* **Time travel:** `prices_as_of(timestamp)` returns the whole price table as known at any moment;
  `price_history(ticker, date)` lists every version of one stock-day.

```sql
-- what the dashboard believed at 10 AM on 13 Oct vs now
SELECT * FROM prices_as_of('2026-10-13 10:00+05:30') WHERE ticker = 'MARUTI.NS' AND price_date = '2026-10-13';
SELECT * FROM price_history('MARUTI.NS', '2026-10-13');
```

### 2.2 Revision taxonomy — `classify_revision()`, `v_price_revisions`
Each change from one version to the next is classified by rules evaluated in order:

| Kind | Rule |
|---|---|
| `intraday_as_close` | the replaced version was stored before the session was final (16:30 IST) |
| `hourly_fill_replaced` | the replaced version was rebuilt from hourly bars |
| `dividend_adjustment` | close unchanged, adjusted close changed |
| `split_or_bonus_adjustment` | close and adjusted close rescaled by the same factor outside 0.8–1.25 |
| `volume_revision` | only volume changed |
| `ohl_revision` | only open/high/low changed |
| `price_restatement` | the close itself changed for any other reason |
| `deleted` | the row disappeared |

`v_settlement` gives, per revised stock-day, the first-print value, the final value, the error of
the first print in basis points and how long after the session the value settled.

### 2.3 Weekly revision scan — `src/revision_scan.py`
The daily load re-checks the last 5 sessions. Once a week the full 2015–today history of every stock
is downloaded again and upserted; anything Yahoo has changed becomes a classified, run-stamped
version. Unchanged rows are not rewritten.

### 2.4 Official reconciliation — `sql/09_reconciliation.sql`, `src/official_prices.py`
NSE and BSE publish an official end-of-day file (bhavcopy, UDiFF format since July 2024) for every
session. The job downloads it, matches our 25 stocks (NSE by symbol and series `EQ`; BSE by ISIN,
learned from the NSE file, falling back to symbol) and stores the official open/high/low/close,
last traded price and volume in `reference_prices`. Every download attempt is logged
(`ok / not_published / blocked / error`) because exchanges may refuse cloud servers; the job never
fails the pipeline.

* `v_source_agreement`: Yahoo vs official close per stock-day. *Agree* = within one tick (₹0.05) or
  1 bp. Also flags when Yahoo's close equals the **last traded price** instead of the official close
  (NSE's close is a 30-minute volume-weighted average, so the two differ), compares volume, and
  compares the **first print** (from `price_versions`) with the official close for days captured live.
* `v_consensus_close`: best available close (NSE > BSE > Yahoo) and the NSE–BSE spread.

### 2.5 Trust score — `sql/10_trust.sql`
Every stored price gets a 0–100 score from four pieces of evidence (official agreement 40,
stability 25, provenance 20, sanity 15) and a grade A/B/C/D with human-readable flags
(`disagrees_with_exchange`, `revised_once`, `not_settled`, `rebuilt_from_hourly`, …).
Unverified history cannot reach grade A by design: A means *checked against the exchange*.

### 2.6 Signal-fragility study — `src/fragility.py`
1. **Replica, validated.** The SQL signal engine (`sql/03_signals.sql`) is re-implemented in
   numpy/pandas. Its output is compared with the signals stored in the database on every run and
   the agreement rates are saved with the results (`fragility_runs.validation`). A unit test proves
   that the single-day closed form used below equals a full recomputation of the perturbed series.
2. **Perturb, recompute, count.** For each scenario the inputs of a stock-day are replaced the way a
   real error would replace them; every window ending at that day is updated exactly; a *flip* is a
   different answer for: trend label, anomaly flag, 52-week breakout, strong breakout, RSI zone
   (<30 / 30–70 / >70), sign of 20-day momentum, and the strongest sector of the day.
3. **Scenarios**

| Scenario | Model | Calibrated by |
|---|---|---|
| `intraday_snapshot` | a mid-session load: price = Brownian bridge from open to close at a random time f ∈ [0.05, 0.95] of the session, clipped to [low, high]; volume = f × day volume; one f per day, shared by every stock (one run is one moment) | the 13 Oct incident (whole market affected at once) |
| `early_snapshot` | same with f ∈ [0.02, 0.10] (first half hour, like 09:39 IST) | 13 Oct |
| `hourly_rebuild` | close error and volume ratio drawn from observed revisions | `v_price_revisions` (default N(0, 4 bp) until enough are observed) |
| `official_vs_yahoo` | recompute with the exchange's official close and volume | `reference_prices` |
| `calibrated_noise` | everyday Yahoo-vs-official differences, bootstrapped | `v_source_agreement` |
| `observed_first_print` | the first version actually received vs the settled value | `price_versions` |
| `adjustment_basis` | whole history on raw close instead of dividend-adjusted close | — |

4. **Uncertainty.** One error hits every stock of a day at once, so stock-days are not independent. Each flip rate has a 95% cluster-robust interval (cluster = contaminated day × simulation; ratio-estimator standard error, Wilson on clusters when no or all rows flip).
5. **Decisions, not just flags.** The monthly "hold the strongest sector" backtest is re-run with
   corrupted month-end data (every month, 25% of months, 5% of months) and reports how often the
   held sector changes and the resulting range of the final value of ₹100.

Why most signals are *not* affected by dividend back-adjustment: rescaling all prices before a date
by one factor leaves every return and every price-to-average ratio computed *entirely* before or
entirely after that date unchanged; only windows that straddle the ex-date move. The
`adjustment_basis` scenario measures exactly that residual.

## 3. How to run

| What | How |
|---|---|
| everything, weekly | *Data trust study* workflow (Sundays, or **Run workflow**) |
| first run | Run workflow with `official_days = 250` (≈ one year of exchange files) |
| daily | the daily workflow also fetches the last 5 official files |
| report | `python src/trust_report.py` (Markdown) or `--json` |
| tests | `python -m unittest discover -s tests -v` (database tests need `TEST_DATABASE_URL`) |
| a file downloaded by hand | `python src/official_prices.py --file NSE BhavCopy_….csv.zip --date 2026-10-15` |

## 4. Limitations (stated up front)
* **Short live history.** Live revision capture starts on 11 Oct 2026; the weekly full-history scan
  and the official reconciliation provide the larger samples.
* **Official files may be unreachable from cloud servers.** Exchanges block some automated
  downloads; availability is logged and reported, never hidden. A file downloaded by hand can be
  loaded with `--file`.
* **The intraday model is a model.** A Brownian bridge inside the day's range is a standard,
  simple assumption, not the true path; the early-snapshot scenario brackets the real 09:39 case.
* **Survivorship and size.** 25 of today's large caps; results may differ for small caps, where data
  errors are probably more common.
* **One free source.** The method applies to any feed; the numbers are for Yahoo Finance's NSE data.

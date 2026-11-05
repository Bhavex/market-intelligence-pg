# Research plan

## Question

**When free market data is wrong or revised, which research conclusions change?**

Free end-of-day data (here, Yahoo Finance for NSE stocks) is what most student, hobbyist and many academic studies
use. It is revised after publication, sometimes stores an intraday value as a close, is re-adjusted for corporate
actions, and occasionally disagrees with the exchange's own file. The question is not "how often is the data wrong?"
on its own. It is whether the **conclusions** drawn from the data survive those errors and revisions.

## Conclusions under test

The project already produces conclusions that can be re-checked:

| Conclusion | Where it comes from |
|---|---|
| C1. Momentum top 5 and sector rotation do **not** beat equal-weight buy-and-hold after costs (9.6% and 10.2% vs 12.5% CAGR, May 2015 – Nov 2026). | `docs/BACKTEST.md` |
| C2. Monthly equal-weight rebalancing **does** beat buy-and-hold (14.0% CAGR, Sharpe 0.98). | `docs/BACKTEST.md` |
| C3. The daily signals (trend, anomaly, 52-week breakout, RSI zone) flip on a measurable share of days when one price is wrong. | `docs/TRUST.md`, `fragility_results` |
| C4. Sector-strength rankings on a given day. | `v_sector_strength` |

## Data this branch now collects

| Evidence | Table | Starts |
|---|---|---|
| Every fetch, with settings and response hash | `fetch_log`, `fetch_settings` | deployment of this branch |
| Every distinct value observed, with time | `price_observations` | deployment |
| What was used, and when it changed | `price_selection`, `price_versions` | deployment / Oct 2026 |
| Quality decisions as they were made | `quality_states` | deployment |
| Prospective revisions: the last 10 sessions daily, plus a rotating month of history | `revision_captures`, `revision_capture_coverage` | first scheduled run |
| Official close vs Yahoo, classified | `reconciliation_results` | first manual run |
| Universe as it was on each date | `universe_membership` | seeded from evidence in the database |
| Which expected sessions are present, missing, failed, suspended or not yet published | `v_session_coverage` | computed on demand |
| Every backtest evaluation, including every look at the holdout | `backtest_trials` | first backtest run after deployment |
| Human labels for a stratified sample of flagged and unflagged rows | `scripts/adjudication_sample.py` output | first labelling round |

## Method

1. **Measure the error process.**
   - From `revision_captures` and `v_revision_capture_rate`: how often values change, by field, size, age of the row
     and company size.
   - From `reconciliation_results`: how often Yahoo disagrees with NSE, and in which class (last price as close, stale
     value, split basis, intraday, unexplained).
2. **Re-run each conclusion on alternative histories:**
   - **(a)** first-print values: `observations_as_of` at each session's close + 1 day;
   - **(b)** today's revised values;
   - **(c)** official bhavcopy closes where available;
   - **(d)** the quality-masked history (suspect and rejected removed as known at each decision);
   - **(e)** today's universe against the dated universe;
   - **(f)** simulated errors at the measured rates (the existing fragility study, recalibrated with the new
     measurements).
3. **Decide whether a conclusion "changes"** using pre-stated thresholds, so the answer cannot be tuned afterwards:
   - C1/C2: the sign of the excess CAGR over buy-and-hold flips, or the Sharpe ranking against the benchmark flips;
   - C3: a flip-rate confidence interval excludes the baseline;
   - C4: Spearman rank correlation with the baseline ranking falls below 0.8.
4. **Measure the detector** before trusting it:
   - two people label a stratified, blind sample (`scripts/adjudication_sample.py`);
   - `scripts/adjudication_score.py` reports precision, false-rejection rate, miss rate and Cohen's κ;
   - the measured error rates calibrate (f).
5. **Guard against our own overfitting:**
   - parameters are chosen on the development period only, and the final 3 years are held out;
   - every evaluation is registered (`backtest_trials`), and the deflated Sharpe probability uses all of them;
   - results are reported at zero, realistic and high costs, with block-bootstrap intervals.
6. **Report every conclusion,** including those that do not change. The write-up follows `docs/paper/outline.md`.

## Threats to validity

- **Short observation window.** Prospective capture starts at deployment, so first-print histories (a) exist only from
  then. Earlier periods rely on `price_versions` (Oct 2026 onward) and on simulation (f).
- **One free source.** The BSE feed (`alt_feed_prices`) and the bhavcopy are the only independent checks.
- **Survivorship in the research universe.** It is today's NIFTY 50 plus a frozen small-cap sample; only the dashboard
  universe has dated membership so far.
- **Synthetic tests.** Every component is tested on synthetic data and fixture files; real-data behaviour is measured
  only by the scheduled and manual workflows.
- **NSE access.** Bhavcopy downloads can be blocked from cloud runners; coverage is reported, not assumed.

## First milestones

1. Deploy this branch; confirm `v_tata_hero_switch.evidence` on production.
2. Run **NSE bhavcopy reconciliation** for the last 250 sessions; publish `v_reconciliation_summary`.
3. Let **Prospective revision capture** run for 8 weeks; publish `v_revision_capture_summary` and the rate table.
4. Re-run the **Signal backtest (real data)** with dated membership and quality masking; compare with C1/C2.
5. Label the first adjudication sample (200 flagged, 200 unflagged) with two people; publish the scores.
6. Write up which conclusions changed, following `docs/paper/outline.md`.

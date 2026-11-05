# Paper outline

**Working title:** *When free market data is wrong or revised, which research conclusions change? A point-in-time
database for NSE end-of-day prices*

**Status:** this is an outline only. It reports no results. The single real-data result that exists so far (the Neon
backtest, 4 May 2015 to 4 Nov 2026, `docs/BACKTEST.md`) was produced **before** the lineage, quality-state, ledger and
rigour code on this branch, so it is a baseline to re-run, not a finding of the paper. Every section lists the
artefacts that will supply its content. References are in [`references.md`](references.md); anything not checked
against the publisher is marked **verify before citing**.

---

## 1. Problem and research question

- **Problem.** Most student, hobbyist and many academic studies of Indian equities use free end-of-day data (here, Yahoo
  Finance through `yfinance`). That data is revised after first publication. It is re-adjusted for corporate actions,
  sometimes stores a last-traded price or an intraday value as the close, and sometimes disagrees with the exchange's own
  file. Studies usually download the history once and treat it as fixed.
- **Research question (RQ).** When free market data is wrong or revised, which research conclusions change?
- **Sub-questions.**
  - **RQ1, the error process.** How often, by how much, and for which rows do values change after first publication?
    How often does the vendor disagree with the NSE bhavcopy, and in which way?
  - **RQ2, detection.** How accurate are the pipeline's flags? Measure precision, false-rejection rate and miss rate
    against two independent human labellers.
  - **RQ3, conclusions.** Do the project's conclusions C1–C4 (`docs/RESEARCH_PLAN.md`) survive alternative histories,
    using thresholds stated before the comparison?
  - **RQ4, the system.** Which database features make RQ1–RQ3 answerable at all? Candidates are append-only
    observations, selection history, as-of queries, dated identity and membership, a fetch ledger, run leases and
    session coverage.
- **Contribution claims to test, not assert.**
  1. A design and an open implementation (PostgreSQL) that records what was known, when, and from which request.
  2. Prospective measurements of revision and disagreement rates for NSE large caps from free data.
  3. A sensitivity analysis of a small set of common backtest conclusions to those errors.
  4. A labelled adjudication sample with inter-rater agreement.

## 2. Related work

Grouped as in `references.md`. One paragraph per group, saying what this paper borrows and where it differs.

- **Data validation and data-quality assessment.** Schema and constraint checks, unit tests for data, quality
  dimensions (accuracy, timeliness, completeness). *We borrow:* declarative checks and the accuracy/timeliness split.
  *We differ:* every check is versioned and its decision is stored with the time it was made.
- **Error detection and data repair.** Rule-based, statistical and probabilistic repair; benchmark studies of error
  detectors. *We differ:* nothing is repaired in place. Decisions are appended, and the backtest uses the decision known
  at the time.
- **Temporal databases and provenance.** Valid time against transaction time, bitemporal tables, SQL:2011 periods,
  why/where provenance. *We borrow:* transaction-time history (`price_versions`, `price_selection`) and per-row
  provenance (`fetch_log`, `price_observations`). *We differ:* the history is kept for a vendor feed outside our
  control, and as-of queries cover quality decisions and universe membership as well as values.
- **Financial data errors and survivorship.** Known error and revision problems in commercial databases, delisting and
  survivorship bias, revisions of analyst data. *We differ:* free data, an emerging market, and prospective capture
  instead of comparing two vintages after the fact.
- **Backtest overfitting and inference on Sharpe ratios.** Data snooping, multiple testing, the deflated Sharpe ratio,
  the probability of backtest overfitting, bootstrap inference for dependent returns. *We borrow:* a trial registry, a
  held-out final period, block bootstrap and the deflated Sharpe ratio. *We add:* data error as a further source of
  false discovery, alongside parameter search.

## 3. Data and provenance

| Source | What | How it is stored | Point-in-time since |
|---|---|---|---|
| Yahoo Finance via `yfinance==1.8.0` | daily OHLCV, `close` split-adjusted, `adj_close` split and dividend adjusted | `fetch_log` (request, settings, hash of the canonical frame), `price_observations`, `daily_prices` | deployment of the research-foundation branch |
| NSE bhavcopy (legacy CM and CM-UDiFF) | official close, last price, previous close, unadjusted | `fetch_log` (hash of the raw bytes), `reconciliation_results` | first manual reconciliation run |
| NSE official prices, BSE feed | independent closes | `reference_prices`, `alt_feed_prices` | existing workflows |
| NSE holiday calendar | trading sessions | `market_holidays`, `calendar_coverage` (2026 only; earlier years inferred) | seed |
| Universe | dashboard 25 and research universes, dated | `instruments`, `instrument_symbols`, `universe_membership` | seeded from database evidence (`v_tata_hero_switch`) |

Items to report:
- The row counts with their definitions (`docs/CLAIMS.md` §2).
- Coverage by status from `v_session_coverage_summary`.
- The fetch-settings table (`v_price_semantics`).
- What is **not** point-in-time: values before deployment, and the research universe's membership.

## 4. Method

1. **Fetch ledger and observations** (`docs/DATA_LINEAGE.md`). Each request is logged. Every distinct value seen is
   appended, and every change to the selected value is appended by a trigger.
2. **Quality states.** Hard failures are rejected at ingest. Jumps beyond ±20% are `suspect`. Later runs append
   transitions using only the evidence then available (`reversed_next_session`, `jump_persisted`,
   `reconfirmed_by_later_fetch`, `revised_value`).
3. **Prospective revision capture.** Each weekday, re-fetch the last 10 sessions and one rotating month, and record
   every differing field with its coverage denominator.
4. **Reconciliation with the bhavcopy.** Match on instrument and date, and classify each pair: `match`,
   `last_traded_not_close`, `previous_session_close`, `split_or_bonus_basis`, `intraday_value`, `unexplained`,
   `missing_in_yahoo` or `missing_in_bhavcopy`.
   - *Note on the reference price.* NSE's official closing price is not simply the last traded price. NSE computes
     it from trades near the end of the session (understood to be a volume-weighted average over the final minutes), so
     close and last can differ on any day. That is why `last_traded_not_close` is its own class.
     **Verify against NSE documentation before citing** both the method and the length of the window.
5. **Session coverage.** Build expected ticker-session pairs from the calendar and dated membership. Classify each one
   as present, holiday, suspended, not yet published, fetch failed or missing.
6. **Run safety.** A database lease with a heartbeat and fencing keeps runs from overlapping. A stale-run detector
   closes dead runs. Crash tests show that killing a run loses and duplicates nothing.
7. **Adjudication.** Draw a stratified sample of flagged and unflagged rows, have two blind labellers label them
   independently, then resolve disagreements in a third file.
8. **Backtest ledger.** Shares and cash, next-session execution, fees funded from cash, and splits, bonuses and
   dividends handled on the declared price basis. The ledger is checked against hand-calculated fixtures.

## 5. Experimental design

- **Alternative histories** (from `docs/RESEARCH_PLAN.md`):
  - (a) first-print values from `observations_as_of`;
  - (b) today's revised values;
  - (c) bhavcopy closes;
  - (d) the quality-masked history;
  - (e) today's universe against the dated universe;
  - (f) simulated errors at the measured rates.
- **Pre-stated decision rules** for "a conclusion changes":
  - C1/C2: the sign of the excess CAGR flips, or the Sharpe ranking against buy-and-hold flips;
  - C3: the flip-rate interval excludes the baseline;
  - C4: Spearman ρ < 0.8 against the baseline ranking.
- **Overfitting controls.**
  - Every evaluation is appended to `backtest_trials`.
  - Parameters are chosen on the development period only; the final 3 years are held out.
  - Deflated Sharpe probability, computed over all registered trials.
  - Block bootstrap with 21-session blocks and 2,000 draws.
  - Costs at zero, realistic (11.2 bp) and high (41.2 bp).
- **Adjudication sample.** 200 flagged and 200 unflagged rows by default, stratified by reason and discrepancy size and
  spread across tickers and years. Two labellers.
- **Timeline.** Collect for 8 weeks (`docs/RESEARCH_PLAN.md` milestones). Any later look at the holdout is counted in
  `v_backtest_holdout_looks`.

## 6. Metrics

| Question | Metric | Source |
|---|---|---|
| RQ1 | revision rate per field, by age and size; disagreement rate per class | `v_revision_capture_rate`, `v_reconciliation_summary` |
| RQ1 | coverage by status | `v_session_coverage_summary`, `v_session_coverage_by_ticker` |
| RQ2 | precision (strict and lenient), false-rejection rate, miss rate, with Wilson 95% intervals; stratum-weighted rates; Cohen's κ | `scripts/adjudication_score.py` |
| RQ3 | CAGR, Sharpe, max drawdown with bootstrap intervals; excess over buy-and-hold; turnover and cost drag; deflated Sharpe probability | `src/backtest_rigour.py` |
| RQ3 | flips of each conclusion under (a)–(f) | to be written: one script that runs the rigour suite per history |
| RQ4 | runs closed by the detector; lease overlaps (must be 0); lineage write failures | `v_stale_runs`, `v_lease_overlaps`, run messages |

## 7. Threats to validity

- **Internal.**
  - Quality rules are heuristics, so RQ2 measures them instead of assuming them.
  - Labellers can see that the evidence is missing for rejected rows that were never stored.
  - Dividends are credited on the ex-date in the ledger, not on the payment date.
  - Holidays before 2026 are inferred from the absence of any price row.
- **Construct.** "Real error" is judged by people against the evidence available to them. Some rows will stay
  `unknown`. Every rate is reported with the number of unknowns.
- **External.**
  - One free vendor and 25 to 81 large and small caps on one exchange.
  - The window is short for prospective capture.
  - The research universe is today's NIFTY 50 plus a frozen small-cap sample, so it is not survivorship-free.
- **Statistical.**
  - The bootstrap assumes stationarity within blocks.
  - The deflated Sharpe ratio assumes the registered trials are the whole search. Trials run before the registry
    existed are not in it.
  - Small strata give wide Wilson intervals.
- **Engineering.** Every component is tested on synthetic data and fixtures. Real-data behaviour comes only from the
  scheduled and manual workflows.

## 8. Reproducibility

- **Code.** Tag a release of this repository; every SQL migration is idempotent (`src/setup_db.py`, applied twice in
  CI).
- **Environment.**
  - `requirements.txt` pins `yfinance==1.8.0`, and the installed version is recorded in `fetch_settings`.
  - PostgreSQL 17 in CI and on Neon; 16 also passes locally.
- **Data.**
  - The fingerprint of the backtest panel is in `rigour_summary.json` and in every `backtest_trials` row.
  - Fetch hashes are in `fetch_log`.
  - Raw vendor files are not redistributed; derived tables follow the dataset already published on Zenodo (README
    badge). **Check the vendor terms before releasing any raw prices.**
- **Commands.**
  - `python src/strategy_backtest.py --source db --rigour`
  - `python scripts/adjudication_sample.py`, then `scripts/adjudication_score.py`
  - `python src/reconcile.py --days 250`
  - `python src/revision_capture.py`
  - The workflows: "Signal backtest (real data)", "NSE bhavcopy reconciliation" and "Prospective revision capture".
- **Tests.**
  - `python -m pytest` runs without a database.
  - The CI integration job runs the database tests against a throwaway PostgreSQL 17.

## Preliminary results (not findings yet)

**NSE bhavcopy reconciliation, 4 Nov 2026** ([run 41911480806](https://github.com/Bhavex/market-intelligence-pg/actions/runs/41911480806)).
This is a single run over 5 sessions (29 Oct to 4 Nov 2026), 81 instruments a session, 405 stock-days in total.
- All 5 NSE files downloaded from a GitHub runner.
- 397 stock-days matched, 4 were `last_traded_not_close` and 4 were `intraday_value`, with no other class. That is 8
  disagreements (2.0%, Wilson 95% interval about 1.0% to 3.9%).
- Not yet known: which tickers they were, whether they persist after revision capture, and the rate over 250 sessions.

Use it only as a pilot for RQ1. It shows that the method runs end to end, not how often the vendor is wrong.

## 9. What exists and what is pending

| Part | State |
|---|---|
| Lineage, quality states, ledger, revision capture, instruments, bhavcopy | merged (research-foundation) |
| Run lease, session coverage, backtest ledger, rigour suite, adjudication tool | this branch |
| Eight weeks of prospective capture | pending deployment |
| First bhavcopy reconciliation | 5-session pilot run on 4 Nov 2026 (8 of 405 disagree); the 250-session run is pending |
| Labelled adjudication sample | pending two labellers |
| Alternative-history runner for RQ3 | not written |
| Any result in this paper | none yet beyond the 5-session reconciliation pilot |

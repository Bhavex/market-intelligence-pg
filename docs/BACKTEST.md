# Backtest: do the momentum and sector-strength signals beat buy-and-hold?

**Short answer: no.** On **real data from the production Neon database, 4 May 2015 to 4 Nov 2026** (11.5 years),
after realistic NSE trading costs, both signals trail an equal-weight buy-and-hold of the same 25 stocks:

| | CAGR | Sharpe |
|---|---:|---:|
| Momentum top 5 | **9.6%** | 0.60 |
| Sector strength | **10.2%** | 0.59 |
| Buy-and-hold | **12.5%** | 0.89 |

Momentum loses to buy-and-hold even before costs. The only rule that beats it is plain monthly rebalancing to equal
weight (14.0% a year, Sharpe 0.98).

Code: `src/strategy_backtest.py` on the ledger in `src/portfolio.py` · tests: `tests/test_strategy_backtest.py` (16),
`tests/test_portfolio.py` (25 hand-calculated fixtures), `tests/test_backtest_rigour.py` (13) · results:
`results/backtest/summary_neon_2015-05_2026-11.csv` · run: [Signal backtest (real data) #41892962103](https://github.com/Bhavex/market-intelligence-pg/actions/runs/41892962103)

## Strategies

| Strategy | Rule, decided at each month-end close | Held |
|---|---|---|
| **Momentum top 5** | rank the 25 stocks by their 60-session return (`momentum_60d`); buy the top 5, equal weight | one month |
| **Sector strength top 1** | average 20-session return of each sector's 5 stocks (`v_sector_strength`); buy every stock of the top sector, equal weight | one month |
| Equal-weight, monthly rebalanced | all stocks, reset to equal weight each month | one month |
| **Benchmark**: equal-weight buy-and-hold | all 25 stocks bought once at equal weight, then left to drift | whole period |

## Assumptions, and what they do not rule out

| Risk | How it is handled |
|---|---|
| **Look-ahead bias** | The weight function receives only rows up to and including the signal date: `px.iloc[:t+1]`. A test spies on every call and asserts that the last row seen is the signal date. |
| **Trading at a price you couldn't get** | Trades fill at the close **one session after** the signal (`lag_days = 1`); the portfolio earns nothing from the signal day itself. A test asserts that every trade lands the day after a signal date. |
| **Future data changing past decisions** | A test multiplies all prices after a cut-off by up to 5× and asserts every earlier holding is unchanged. |
| **Transaction costs** | **11.2 bp one way** on every rupee traded. This is NSE delivery with zero brokerage: STT 10 bp each side, stamp duty 1.5 bp on the buy, exchange and SEBI fees, and GST, from `src/india_costs.py`. Before it was used here, the cost model was checked by reproducing a previously stored cost-adjusted return series to within 1×10⁻⁶. |
| **Daily drift** | Between rebalances, weights drift with each stock's daily return; turnover is measured against the drifted weights, not the targets. |
| **Warm-up** | Signals need 60 sessions of history, so the first trade is on **4 May 2015**. All four series start that day, so they are compared over the same period. |
| **Revised data** | The price history is the database's *current* values, so `adj_close` already includes later dividend and split rescaling, and any later Yahoo corrections. Decision timing has no look-ahead; the data is not point-in-time. |
| **Survivorship** | The Neon run below used *today's* 25 large caps, which flatters every strategy, the benchmark included, by roughly the same amount. The engine now supports dated membership (`universe_membership`: Tata Motors until its demerger, Hero MotoCorp after it); each decision uses the members on its date, and the benchmark hands a leaver's weight to the joiner. Re-run the workflow after deploying to get that version. |
| **Data quality known at the time** | The engine masks rows whose quality state *at the decision time* was suspect or rejected (`quality_states`). States are recorded from deployment of the new design on; the Neon run below predates it. |
| **Not modelled** | Bid-ask spread, market impact, capital-gains tax, cash yield (Sharpe ratios use a zero risk-free rate). |

Metrics:
- **CAGR:** compound annual growth rate.
- **Volatility:** annualised standard deviation of daily returns.
- **Sharpe:** annualised mean ÷ annualised standard deviation.
- **Max drawdown:** the worst peak-to-trough fall.
- **Hit rate:** share of months the strategy beat the benchmark.
- **Turnover:** rupees bought plus rupees sold, divided by the portfolio value at execution, summed per year (a full
  switch is 2.0).

## Results: real Neon data, 4 May 2015 to 4 Nov 2026

Daily `adj_close` for the 25 dashboard stocks, read from `v_company_daily` with read-only queries by the manual
**Signal backtest (real data)** workflow (`.github/workflows/backtest.yml`). Settings: 11.2 bp, one-session lag; run on
4 Nov 2026.

| ₹100 invested on 4 May 2015 | Final ₹ | CAGR | CAGR before costs | Volatility | Sharpe | Max drawdown | Hit rate vs B&H | Turnover / yr |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Momentum top 5 (60d) | 282 | 9.6% | 11.1% | 18.2% | 0.60 | −33.2% | 48.9% | 12.2× |
| Sector strength top 1 (20d) | 300 | 10.2% | 12.7% | 20.0% | 0.59 | −30.1% | 51.1% | 19.8× |
| Equal-weight, monthly rebalanced | **437** | **14.0%** | 14.0% | 14.4% | **0.98** | −30.7% | 56.1% | 0.7× |
| **Benchmark: equal-weight buy-and-hold** | **379** | **12.5%** | 12.5% | 14.5% | **0.89** | −30.8% | | 0.1× |

![Real Neon backtest](img/backtest_neon_real.png)

**What it shows:**
- **Neither signal beats the benchmark.** After costs, momentum trails buy-and-hold by 2.9 pp a year and sector
  strength by 2.3 pp. Both have a lower Sharpe ratio (0.60 and 0.59 against 0.89) and higher volatility (18–20% against
  14.5%).
- **Momentum fails even before costs** (11.1% against 12.5%). The 60-day winners of these 25 stocks did not keep
  winning the next month.
- **Sector strength's small gross edge is eaten by trading.** Before costs it is 0.2 pp a year ahead (12.7% against
  12.5%). But it trades about 20× its capital a year, so at 11.2 bp per side costs take about 2.2 pp a year and leave it
  2.3 pp behind.
- **The hit rates are coin flips.** 48.9% and 51.1% over about 139 months, where a 95% interval for a fair coin is
  about 42%–58%.
- **Drawdowns are similar for everything** (−30% to −33%). Concentrating in 5 stocks or one sector did not protect
  against the worst fall.
- **Rebalancing is the only thing that helped.** Resetting to equal weight each month earned 1.4 pp a year more than
  buy-and-hold with a better Sharpe (0.98). That points to short-term mean reversion between these large caps, the
  opposite of momentum.

To re-run it: Actions → **Signal backtest (real data)** → Run workflow (inputs: cost in bp, execution lag). The run page
shows the summary table, and the charts are uploaded as the `backtest-output` artifact. Locally:

```bash
DATABASE_URL=postgresql://reader:...@host/db python src/strategy_backtest.py --source db --seeds 0
```

## Engine check on synthetic data (not evidence about the market)

Synthetic random walks (`marketintel/sample.py`) have **no momentum by construction**. A correct backtest must therefore
show no edge, only losses equal to costs and concentration. A leaky one (look-ahead) would show a large gain.

```bash
python src/strategy_backtest.py --source synthetic --seeds 20
```

Across **20 seeds × 10 years**, the mean excess CAGR over buy-and-hold, after costs, is:

| Strategy | Mean excess CAGR | Spread (s.d.) | Mean hit rate |
|---|---:|---:|---:|
| Momentum top 5 | −2.0 pp | 3.9 pp | 48.3% |
| Sector strength top 1 | −5.2 pp | 4.0 pp | 44.7% |
| Equal-weight rebalanced | −0.4 pp | 0.9 pp | 47.4% |

![Synthetic seed study](img/backtest_synthetic_seeds.png)

That is what an honest engine should produce, and the real results above look much the same. A single synthetic path
can still mislead: on seed 7 alone sector strength *beats* buy-and-hold, purely by chance
(`img/backtest_synthetic.png`). That is why the seed study, not one run, is the check.

## Tests

`tests/test_strategy_backtest.py` checks the following:
- no look-ahead: weight functions see only data up to the signal date;
- trades happen the day after a signal;
- shocking future prices leaves past decisions unchanged;
- with flat prices a buy-and-hold loses exactly its entry cost;
- costs scale with turnover and leave gross returns untouched;
- the stored series used to validate the cost model is reproduced;
- a strongly trending sector is detected;
- the metric formulas are correct, and the engine is deterministic.

## Bottom line for a demo

The project's signals are **descriptive analytics**: they show which sector is leading and which stocks are
stretched. They are not a trading edge. On 11.5 years of real data, neither momentum nor sector rotation beat simply
holding the 25 stocks, and monthly rebalancing did better than both.

## Accounting: a share-and-cash ledger checked by hand

Since the research-rigour branch, `simulate()` runs on `src/portfolio.py`. That ledger holds **shares and cash**, not
weights:

| Rule | What the ledger does |
|---|---|
| Execution | An order decided at the close of day *t* fills at the close of *t + lag* (default 1), or at a supplied open. A signal on the last session never fills. |
| Costs | `cost_bp` on every rupee traded, each side. Sells are filled first; buys are funded only from the cash left after fees, so the portfolio never borrows. |
| Splits and bonuses | On the ex-date (or the next session if it falls on a weekend), shares × ratio and the last price ÷ ratio, for `basis="unadjusted"`. `split_adjusted` prices skip them; `total_return` prices (`adj_close`, what the backtest uses) refuse explicit actions so nothing is counted twice. |
| Dividends | Cash per share × shares held at the ex-date open, credited on the ex-date. Not paid on shares bought that day. |
| Missing prices | A held stock with no close is valued at its last close. An order leg with no price stays open and fills on the next session that has one; other legs fill now. A sell that cannot fill does not fund a buy. After `max_open_sessions` (5) the leg is dropped. |

Every number in `tests/test_portfolio.py` was worked out by hand before the test was run. Two examples:

| Fixture | Hand calculation | Expected |
|---|---|---|
| Rebalance | ₹100 cash; A = 10, B = 10. Buy 5 A and 5 B. A doubles to 20, so NAV = 5·20 + 5·10 = 150. Rebalance to 75/75: sell 1.25 A (₹25), buy 2.5 B (₹25) | NAV 150; turnover 50 / 150 = 0.3333 |
| 1% costs | Buy with ₹100 at 10: shares = 100 / 1.01 / 10 = 9.900990099. Sell at 12: 118.8118812 × 0.99 | NAV 117.6237624; fees 0.9900990 + 1.1881188 |

The old weight-based engine gave the same results before costs. After costs it charged fees as a multiple of turnover,
not on the rupees actually traded. On the 10-year synthetic panel the two engines differ by at most 0.02 pp CAGR, and
the 20-seed study is unchanged to four decimals.

## Rigour: costs, uncertainty, a holdout and a trial registry

`python src/strategy_backtest.py --source db --rigour` (and the **Signal backtest (real data)** workflow) adds:

| Output | What it is |
|---|---|
| `cost_sensitivity.csv` | Every strategy at **zero**, **realistic (11.2 bp)** and **high (41.2 bp)** one-way costs. "High" adds 30 bp for spread and impact, which the realistic schedule leaves out. Reported for the full, development and holdout periods. |
| `turnover.csv` | Turnover per year, trade days, mean turnover per trade day, fees as % of mean NAV, and cost drag (CAGR before minus after costs). |
| `bootstrap_ci.csv` | 95% moving-block bootstrap intervals (21-session blocks, 2,000 draws, fixed seed) for CAGR, Sharpe and max drawdown, plus **paired** intervals for excess CAGR and Sharpe over buy-and-hold, and the share of draws above zero. |
| Chronological split | The final 3 years are the **holdout**. The parameter sweep (momentum: lookback 20/60/120 × top 3/5/8; sector strength: lookback 20/60 × top 1/2) is scored on the development period only. Only the parameters picked there are then evaluated on the holdout. A test shocks the holdout prices and checks that the picks do not change. |
| `backtest_trials` | An append-only table with every evaluation ever run: strategy, parameters, period, cost level, data fingerprint, code version and metrics. `v_backtest_trial_counts` counts evaluations and holdout looks per strategy; `v_backtest_holdout_looks` lists each run that looked at the holdout. Without the table (local runs), the registry is `trial_registry.csv`. |
| Deflated Sharpe probability | Bailey and López de Prado's correction, computed over all development-period trials of a strategy in the registry. More trials raise the bar. |
| `rigour_summary.md` | The tables above, appended to the workflow's job summary. |

The committed synthetic outputs are in `results/backtest/` (`rigour_summary.md` and friends). They are synthetic and
show the format only. The Neon numbers above predate the ledger and the rigour suite; re-run the workflow for the
current version.

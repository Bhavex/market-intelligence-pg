# Backtest: do the momentum and sector-strength signals beat buy-and-hold?

**Short answer: no, not on a risk-adjusted basis.** On real data from Feb 2015 to Oct 2026, after realistic NSE trading
costs, the sector-strength rotation finishes **0.2 pp a year above** an equal-weight buy-and-hold of the same 25 stocks.
It does that with **50% more volatility**, a **lower Sharpe ratio (0.62 vs 0.84)** and a **deeper drawdown (−31.6% vs
−19.4%)**. A plain equal-weight portfolio rebalanced monthly beats both. The stock-level momentum rule could not be
tested on real prices from this environment (see [Data](#data)). The engine and a workflow that runs it on the
production database are included.

Code: `src/strategy_backtest.py` · tests: `tests/test_strategy_backtest.py` (16) · outputs: `results/backtest/`

## Strategies

| Strategy | Rule, decided at each month-end close | Held |
|---|---|---|
| **Momentum top 5** | rank the 25 stocks by their 60-session return (`momentum_60d`); buy the top 5, equal weight | one month |
| **Sector strength top 1** | average 20-session return of each sector's 5 stocks (`v_sector_strength`); buy every stock of the top sector, equal weight | one month |
| Equal-weight, monthly rebalanced | all stocks, reset to equal weight each month | one month |
| **Benchmark**: equal-weight buy-and-hold | all 25 stocks bought once at equal weight, then left to drift | whole period |

## Assumptions (built so the result can't flatter itself)

| Risk | How it is handled |
|---|---|
| **Look-ahead bias** | The weight function receives only rows up to and including the signal date: `px.iloc[:t+1]`. A test spies on every call and asserts that the last row seen is the signal date. |
| **Trading at a price you couldn't get** | Trades fill at the close **one session after** the signal (`lag_days = 1`); the portfolio earns nothing from the signal day itself. A test asserts that every trade lands the day after a signal date. |
| **Future data changing past decisions** | A test multiplies all prices after a cut-off by up to 5× and asserts every earlier holding is unchanged. |
| **Transaction costs** | **11.2 bp one way** on every rupee traded. This is NSE delivery with zero brokerage: STT 10 bp each side, stamp duty 1.5 bp on the buy, exchange and SEBI fees, and GST, from `src/india_costs.py`. A sector switch sells 100% and buys 100%, so it pays twice. |
| **Daily drift** | Between rebalances, weights drift with each stock's daily return; turnover is measured against the drifted weights, not the targets. |
| **Survivorship** | Not removed. The universe is *today's* 25 large caps (`TATAMOTORS.NS` was replaced after its demerger), which flatters every strategy, the benchmark included, by roughly the same amount. |
| **Not modelled** | Bid-ask spread, market impact, capital-gains tax, cash yield (Sharpe ratios use a zero risk-free rate). |

Metrics:
- **CAGR:** compound annual growth rate.
- **Volatility:** annualised standard deviation.
- **Sharpe:** annualised mean ÷ annualised standard deviation.
- **Max drawdown:** the worst peak-to-trough fall.
- **Hit rate:** share of months the strategy beat the benchmark.
- **Turnover:** sum of |Δweight| per year.

## Results on real data: sector strength

The sector-rotation returns were measured on the production database for Feb 2015 to Oct 2026 and are stored in
`results/revision/task6_monthly_clean.csv` (141 holding months, derived returns only). The engine re-applies costs to
that series from the recorded sector picks. **It reproduces the stored 10 bp series to within 1×10⁻⁶** (the CSV is
rounded to 6 decimals), and the no-cost Sharpe of 0.720 matches the stored summary. It then applies the 11.2 bp
realistic cost.

```bash
python src/strategy_backtest.py --source published
```

| Feb 2015 to Oct 2026, ₹100 invested | Final ₹ | CAGR | Volatility | Sharpe | Max drawdown | Hit rate vs B&H | Positive months |
|---|---:|---:|---:|---:|---:|---:|---:|
| Sector strength, no costs | 463 | 13.9% | 21.2% | 0.72 | −29.9% | 53.2% | 58.9% |
| **Sector strength, 11.2 bp** | **359** | **11.5%** | **21.2%** | **0.62** | **−31.6%** | **51.8%** | 58.2% |
| Equal-weight, monthly rebalanced | 420 | 13.0% | 14.2% | **0.94** | −21.7% | 60.3% | 63.1% |
| **Benchmark: equal-weight buy-and-hold** | **350** | **11.3%** | **13.8%** | **0.84** | **−19.4%** | | 65.2% |

![Real-data sector rotation](img/backtest_real_sector_rotation.png)

**What it shows:**
- **Costs eat the edge.** The rule switches sector in 113 of 141 months (80%), so it trades about 19× its capital a
  year. 11.2 bp per side takes the final value from ₹463 to ₹359, and the 2.7 pp/yr gross lead over buy-and-hold
  shrinks to 0.2 pp/yr.
- **The extra return is paid for with risk.** Holding one sector of five stocks has 50% more volatility than holding
  all 25. The Sharpe ratio is lower than the benchmark's *even before costs* (0.72 vs 0.84).
- **A coin-flip hit rate.** It beats buy-and-hold in 51.8% of months: indistinguishable from chance over 141 months
  (a 95% interval for a fair coin is about 42%–58%).
- **Rebalancing is the real "signal".** The equal-weight monthly rebalanced portfolio has the best Sharpe (0.94),
  which suggests some short-term mean reversion between these stocks rather than momentum.
- **Caveat on timing:** this stored series was computed from the signal at the month-end close, which is the standard
  close-to-close convention and slightly optimistic. The engine's default one-day lag would be stricter. The real-data
  workflow below runs it that way.

## Momentum on real data: not run here

The momentum rule needs daily prices for every stock. This environment has no access to the production database, and
outbound requests to Yahoo Finance are blocked (HTTP 403 from the network proxy). By repository policy, raw vendor
prices are never committed. **So no real-data momentum result is reported here.** I did not substitute a synthetic
number for one. To produce it, run the manual workflow **Signal backtest (real data)**
(`.github/workflows/backtest.yml`). It reads `v_company_daily` with read-only queries, runs all four strategies with the
one-day lag and 11.2 bp costs, writes the summary table to the run page, and uploads the charts as an artifact.
Locally:

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

That is what an honest engine should produce. Momentum trades about 11.6× a year (≈1.3 pp of costs). Sector strength
trades about 16.7× (≈1.9 pp) and also pays a volatility drag for holding only 5 correlated stocks. Hit rates sit just
below 50%. A single path can still mislead: on seed 7 alone sector strength *beats* buy-and-hold (₹176 vs ₹144,
`img/backtest_synthetic.png`), purely by chance. That is why the seed study, not one run, is the check.

![Synthetic single run](img/backtest_synthetic.png)

## Tests

`tests/test_strategy_backtest.py` checks the following:
- no look-ahead: weight functions see only data up to the signal date;
- trades happen the day after a signal;
- shocking future prices leaves past decisions unchanged;
- with flat prices a buy-and-hold loses exactly its entry cost;
- costs scale with turnover and leave gross returns untouched;
- the stored 10 bp series is reproduced;
- a strongly trending sector is detected;
- the metric formulas are correct, and the engine is deterministic.

## Bottom line for a demo

The project's signals are **descriptive analytics**: they show which sector is leading and which stocks are
stretched. They are not a trading edge. On real data the only rule that improves on buy-and-hold is rebalancing, and
the sector-rotation signal's apparent gain disappears once you count NSE's statutory costs and the risk it takes.

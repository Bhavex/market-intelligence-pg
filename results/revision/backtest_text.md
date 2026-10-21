# Replacement text: the sector-rotation backtest as an illustration of data-error sensitivity (round 3)

**Read first.**
1. **Sample and costs.** Everything is on the database through **19 Oct 2026** (the paper's sample): break-even **3.2 bp**, Sharpe **0.72 (rotation) vs 0.95 (equal-weight rebalanced)**, drawdowns -29.9% / -21.7%. The realistic cost is **11.2 bp one way**, from NSE's current delivery schedule (`nse_delivery_cost_schedule.csv` in `zenodo_v2/`, `src/india_costs.py`). The 13 Oct figures (4.4 bp, 0.94) are not used; they are a different sample.
2. **Monte Carlo numbers are from the pinned study run** (`Data trust study` run 39549402280, 21 Oct 2026: data cutoff 19 Oct, 1,000,000 simulated histories per setting x 3 independent seeds, all seed spreads within Rs 1.30). The old figures (median 397, range 269-517) do not survive: they were sampling noise from 100 histories.
3. **Months.** The series has **142 monthly rebalancing decisions; 141 holding months, the last one partial (through 19 Oct 2026)** (the 142nd decision, taken on 19 Oct for November, has no return yet). Round 2's wording "141 completed months plus a partial October" was wrong by one; this file fixes it.

---

## Abstract: replace the last sentence of the findings

> A monthly sector-rotation backtest, used only as an illustration, ends at a median of about ₹401 instead of ₹459 when every month-end price is an intraday snapshot, but the same rule loses more than that, ₹103, to the 11 bp one-way cost of an ordinary NSE delivery trade even with perfect data, so the exercise shows how sensitive a backtest is to its inputs, not that the rule is worth trading.

(Alternatively drop the backtest from the abstract.)

## Section 4, replace the paragraph "Backtest sensitivity"

> **Backtest sensitivity (illustration).** To show how a data error propagates into a result, we use a deliberately simple rule on the 25 dashboard stocks: at each month-end hold, for the next month, the sector (IT, Banking, Auto, Pharma or FMCG) with the highest average 20-day momentum, each sector being an equal-weight, daily-rebalanced average of its five stocks. The rule is not tuned, not tested out of sample and not proposed as a strategy. The universe is today's index members (survivorship bias); the sample is February 2015 to 19 October 2026, 142 monthly rebalancing decisions and 141 holding months, the last one partial (through 19 Oct 2026). We first report the rule's cost sensitivity, then re-run it with an intraday snapshot at every month-end, at 25% of month-ends and at 5%, using the Brownian-bridge model above, with 1,000,000 simulated histories per setting and three independent random seeds; the medians and the 5th and 95th percentiles agree across the three seeds to within ₹1.30. Costs are a fixed number of basis points on every one-way notional traded; a sector switch sells and buys 100% of the portfolio, so it costs twice the one-way rate. Our realistic cost is 11.2 bp one way: 10 bp securities transaction tax (charged on both the purchase and the sale) + 1.5 bp stamp duty (buyer only, so 0.75 bp on average) + 0.31 bp NSE transaction charge + 0.10 bp SEBI fee + 18% GST on the last two, with zero brokerage as at a discount broker (NSE circular NSE/FA/73061 of 27 Feb 2026 and NSE's STT and levies page, accessed 21 Oct 2026). There are no spread, market-impact, depository-charge or capital-gains-tax models, and the risk-free rate is zero in the Sharpe ratios.

## Section 5.4, replace the whole subsection

> ### 5.4 An illustration: one bad month-end versus trading costs
>
> We use a monthly sector-rotation rule only as a test object: its answer depends on a ranking of sectors at each month-end, so a wrong month-end price can change what it holds. We make no claim that the rule is profitable.
>
> **Baseline.** Without costs the rule turns ₹100 into ₹459 (CAGR 13.8%, volatility 21.2%, Sharpe 0.72, maximum drawdown -29.9%). An equal-weight portfolio of the same five sectors, rebalanced monthly, ends at ₹427 (CAGR 13.2%, volatility 14.2%, Sharpe 0.95, maximum drawdown -21.7%). Rotation ends ₹32 higher but with a lower Sharpe ratio and a deeper drawdown: its extra return is paid for with extra risk. It changes sector in 81% of months, 9.7 times the portfolio traded per year against 0.24 for the equal-weight benchmark.
>
> **Data errors.** If every month-end price were an intraday snapshot, the held sector changes in 5.8% of months and the median final value falls to ₹401, a loss of ₹58 (13%); the 90% range is ₹290 to ₹532, wide on both sides. With snapshots at 25% of month-ends the held sector changes in 1.4% of months and the median is ₹457 (a loss of ₹2), the 90% range ₹356-₹519; at 5% it changes in 0.3% of months, the median is unchanged (₹459) and the 5th percentile is ₹435. The one real incident in our data, the 13 Oct load, was not a month-end and moved the final value by less than 0.4%.
>
> **Transaction costs.** The table charges the same strategy a one-way cost on every rupee traded.
>
> | One-way cost | Rotation: final ₹ | Rotation: Sharpe | Equal-weight, monthly rebalanced: final ₹ | Equal-weight: Sharpe | Buy-and-hold, 5 sectors: final ₹ |
> |---:|---:|---:|---:|---:|---:|
> | 0 bp | 458.7 | 0.72 | 427.1 | 0.95 | 380.2 |
> | 5 bp | 409.5 | 0.67 | 425.9 | 0.95 | 380.0 |
> | **11.2 bp (NSE delivery, zero brokerage)** | **355.6** | **0.61** | **424.4** | **0.94** | **379.7** |
> | 20 bp | 291.1 | 0.53 | 422.3 | 0.94 | 379.4 |
> | 30 bp | 231.7 | 0.44 | 420.0 | 0.94 | 379.0 |
>
> Rotation falls below the rebalanced equal-weight portfolio at a one-way cost of **3.2 bp** and below a buy-and-hold of the five sectors at 8.3 bp; its Sharpe ratio is below the benchmark's at every cost, including zero. At the realistic 11.2 bp it ends at ₹356 (CAGR 11.4%, Sharpe 0.61, drawdown -31.6%), against ₹424 for equal weight. The break-even depends on the end date (3.2 bp at 19 Oct, 4.4 bp at 13 Oct, 5.5 bp at 30 Sep), but every one of them is below the realistic cost.
>
> **Comparing the two effects.** The worst data-error case (every month-end wrong) removes ₹58 from the median outcome, which equals a one-way cost of 6.0 bp; the realistic 11.2 bp removes ₹103, almost twice as much, with certainty and on every outcome. The 5th percentile of the worst data-error case (₹290, a loss of ₹169) corresponds to about 20 bp. A 5% rate of month-end errors is worth about 2.4 bp of cost at the 5th percentile and nothing at the median. The two effects differ in kind: data errors mostly widen the distribution of outcomes and lower its median only when they are frequent, whereas costs shift every outcome down in proportion to turnover.
>
> **What this does and does not show.** Data errors matter for a strategy that ranks assets at one date, because a wrong price at that date changes the ranking (5.8% of decisions in the worst case). They did not matter in the observed data: after settlement the free close matches the official one on 99.94% of days, and the one intraday episode was not a month-end. Costs matter more for this rule because it trades most months. Because the rule is simple, in-sample, survivorship-biased and cost-sensitive, its absolute level is not evidence that it can be traded; the exercise shows how input errors and frictions propagate into a backtest.

## Conclusion: replace the backtest clause

> ... and it can move a backtest by tens of rupees when it hits every month-end of an illustrative sector-rotation rule (₹459 to a median of about ₹400), although for that rule an ordinary NSE delivery cost of about 11 bp one way costs more (₹103) and removes its apparent advantage over an equal-weight benchmark.

## New limitation (Section 6)

> **Illustrative backtest.** The sector-rotation rule is a test object. It is in-sample, uses today's index members, ignores spreads, market impact, depository charges and taxes on gains, and uses a zero risk-free rate. Its break-even cost (3.2-5.5 bp depending on the end date) is sample-dependent; only the ordering, costs larger than data errors for this rule, is robust.

## Fixes for the existing text that this replaces
- "142 months" -> "142 monthly rebalancing decisions; 141 holding months, the last one partial (through 19 Oct 2026)".
- "twelve-year backtest" -> "eleven-and-a-half-year (Feb 2015 - 19 Oct 2026)".
- "(142 months, no costs)": costs are now reported.
- State the snapshot: "database through 19 Oct 2026".

---

## Numbers and sources
| Number | Value | Source |
|---|---|---|
| Rotation, 0 bp | ₹458.72, Sharpe 0.7158, maxDD -29.92%, CAGR 13.84%, vol 21.20%, turnover 9.66x/yr, trades 80.9% of months | `src/backtest_revision.py --end 2026-10-19` -> `task2_backtest_19oct_costs.csv`; ₹458.72 equals the paper's run |
| Equal-weight, rebalanced | ₹427.09, Sharpe 0.9467, maxDD -21.67%, CAGR 13.15%, vol 14.17%, turnover 0.24x/yr | same |
| Costs 5 / 11.2 / 20 / 30 bp | rotation 409.5 / 355.6 / 291.1 / 231.7; equal-weight 425.9 / 424.4 / 422.3 / 420.0 | same |
| Realistic cost | 11.98 bp buy, 10.48 bp sell, 11.23 bp one-way average, used 11.2 | `zenodo_v2/nse_delivery_cost_schedule.csv`, `src/india_costs.py` |
| Break-even | 3.2 bp (19 Oct), 4.4 (13 Oct), 5.5 (30 Sep); rotation = buy-and-hold at 8.3 bp | `break_even_bp()` |
| Data error 100% / 25% / 5% | median 400.64 / 456.84 / 458.72; p5 289.56 / 355.58 / 434.82; p95 532.14 / 519.34 / 484.79; held sector changes in 5.75% / 1.44% / 0.29% of months; seed spreads (max-min of 3 seeds): median 0.30 / 0.00 / 0.00, p5 0.64 / 0.07 / 1.30, p95 0.63 / 0.02 / 0.00 | `Data trust study` run 39549402280, `zenodo_v2/backtest_sensitivity.csv` |
| Cost equivalents | ₹58.1 median loss = 6.0 bp; p5 case (₹289.6) = 20.2 bp; 5%-case p5 (₹434.8) = 2.4 bp | solved on the 19 Oct monthly series: the cost at which `backtest_revision` rotation's final value equals the target |
| 13 Oct incident | rotation 462.98 -> 461.45 (-1.53), equal-weight 419.91 -> 415.91 | `src/backtest_revision.py` on the 13 Oct database state (round 2) |

## Things I could not supply
- Spread, market impact, depository charges and taxes on gains: not modelled and not sourced.

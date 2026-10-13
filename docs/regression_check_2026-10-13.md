# Regression check on the analytics report (Adya's tasks 3a, 3b, 3c, 5)

Tool: `python src/adya_snapshot.py --tag <name>` then `--compare <a> <b>`.
Both checks below ran on a **local replica** of the Neon database (same schema, same 72,725 price rows,
loaded from Yahoo on 13 Oct 2026, 23:00 IST), because the production `DATABASE_URL` was not available
in the environment where this work was done. Re-run on production with the same commands (see CHANGES.md).

## A. Did the code changes (52-week columns, new view columns, pipeline rewrite) move any number?

`before_replica` (original code) vs `after_replica` (new code), same data: **IDENTICAL** for 3a (60 rows),
3b (5), 3c (5) and 5 (141). The new view columns are appended at the end and no existing column changed.

## B. What does correcting the stale 13 Oct prices do to the numbers in the report?

Production holds an intraday price for 13 Oct on 24 of 25 tickers (written by the 09:39 IST run; 16 differ
from the official NSE close by more than 0.5%). The replica was put into that state using the closes
published in the dashboard export (`site/data/stocks.json`, generated from production at 04:31 UTC), which
reproduces the report exactly (Pharma +5.04%, IT -24.28%, 461.45 / 415.91, volatility and correlation
tables). The fixed pipeline then healed it (25 rows updated, 100 unchanged).

| Report item | In the report (stale 13 Oct) | After correction | Change |
|---|---|---|---|
| 2026 YTD Pharma | +5.04% | +5.39% | +0.35 pt |
| 2026 YTD Banking | -5.44% | -4.95% | +0.49 pt |
| 2026 YTD Auto | -11.63% | -11.19% | +0.44 pt |
| 2026 YTD FMCG | -17.88% | -17.73% | +0.15 pt |
| 2026 YTD IT | -24.28% | -21.87% | +2.41 pt |
| Volatility, Banking | 22.33% | 22.34% | +0.01 pt |
| Volatility, IT | 21.10% | 21.15% | +0.05 pt (rounds 21.1 -> 21.2 at one decimal) |
| Correlation IT-FMCG | 0.348 | 0.349 | +0.001 |
| Backtest final value, strategy | Rs 461.45 | Rs 462.98 | +1.53 |
| Backtest final value, equal-weight | Rs 415.91 | Rs 419.91 | +4.00 |
| Oct 2026 month, strategy / equal-weight | -1.71% / -0.14% | -1.38% / +0.82% | +0.33 / +0.96 pt |

Everything else in 3a/3b/3c/5 is unchanged (rankings are unchanged: Pharma is still the strongest 2026
sector, IT still the weakest). The Oct 2026 backtest month is only 13 days long and ends on the 13 Oct row,
so the final values depend directly on that one day.

Absolute levels on the replica differ slightly from production in older years only because Yahoo restates
adj_close over time; the stale-to-healed deltas above are the part to trust.

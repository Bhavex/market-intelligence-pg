### Backtest rigour (synthetic, 2016-10-31 to 2026-10-30)

Holdout starts 2023-10-30 (final period, never used to pick parameters). Trials registered this run: 51; in the csv registry: 51.

#### Cost sensitivity (full period)

| strategy | zero | realistic | high |
|---|---|---|---|
| benchmark | 3.7% | 3.7% | 3.7% |
| equal_weight_rebalanced | 4.8% | 4.7% | 4.5% |
| momentum | 0.4% | -0.9% | -4.2% |
| sector_strength | 7.8% | 5.8% | 0.6% |

CAGR. Cost levels in bp one-way: zero 0, realistic 11.2, high 41.2.

#### Turnover (realistic costs)

| strategy | turnover / year | trade days | cost drag (CAGR) |
|---|---|---|---|
| momentum | 11.55 | 117 | 1.3% |
| sector_strength | 16.66 | 117 | 2.0% |
| equal_weight_rebalanced | 0.68 | 117 | 0.1% |
| benchmark | 0.10 | 1 | 0.0% |

#### Block bootstrap, 95% intervals (2000 draws, 21-session blocks)

| strategy | metric | point | interval | P(>0) |
|---|---|---|---|---|
| momentum | cagr | -0.9% | -11.7% to 11.3% | 0.44 |
| momentum | sharpe | 0.04 | -0.61 to 0.68 | 0.56 |
| momentum | max_drawdown | -47.4% | -78.2% to -28.1% | n/a |
| momentum | excess_cagr_vs_benchmark | -4.6% | -10.1% to 1.2% | 0.06 |
| momentum | excess_sharpe_vs_benchmark | -0.27 | -0.60 to 0.06 | 0.05 |
| sector_strength | cagr | 5.8% | -7.1% to 20.2% | 0.80 |
| sector_strength | sharpe | 0.39 | -0.28 to 1.04 | 0.88 |
| sector_strength | max_drawdown | -31.1% | -71.4% to -25.6% | n/a |
| sector_strength | excess_cagr_vs_benchmark | 2.1% | -5.8% to 10.5% | 0.68 |
| sector_strength | excess_sharpe_vs_benchmark | 0.07 | -0.35 to 0.50 | 0.62 |
| equal_weight_rebalanced | cagr | 4.7% | -5.3% to 16.1% | 0.81 |
| equal_weight_rebalanced | sharpe | 0.38 | -0.28 to 1.05 | 0.86 |
| equal_weight_rebalanced | max_drawdown | -31.7% | -61.8% to -21.0% | n/a |
| equal_weight_rebalanced | excess_cagr_vs_benchmark | 1.0% | -0.4% to 2.4% | 0.92 |
| equal_weight_rebalanced | excess_sharpe_vs_benchmark | 0.07 | -0.02 to 0.15 | 0.93 |
| benchmark | cagr | 3.7% | -6.1% to 15.1% | 0.77 |
| benchmark | sharpe | 0.31 | -0.34 to 0.98 | 0.82 |
| benchmark | max_drawdown | -31.7% | -64.4% to -21.8% | n/a |

#### Chronological split

| strategy | development CAGR | holdout CAGR | development Sharpe | holdout Sharpe |
|---|---|---|---|---|
| momentum | -7.6% | 16.0% | -0.34 | 0.92 |
| sector_strength | 1.6% | 15.9% | 0.18 | 0.87 |
| equal_weight_rebalanced | -0.5% | 17.6% | 0.04 | 1.14 |
| benchmark | -0.6% | 14.0% | 0.04 | 0.93 |

#### Parameters picked on the development period only

| strategy | picked | holdout CAGR | holdout Sharpe | trials | deflated Sharpe probability |
|---|---|---|---|---|---|
| momentum | {"momentum_lookback": 20, "momentum_top_n": 8} | 20.7% | 1.20 | 12 | 0.29 |
| sector_strength | {"sector_lookback": 60, "sector_top_k": 1} | 4.4% | 0.32 | 7 | 0.67 |

Version 2 of the open dataset behind "How Much Can You Trust Free Market Data? A Point-in-Time Study of Free End-of-Day Prices for NSE Stocks".

Derived measurements of how Yahoo Finance's free end-of-day prices for NSE stocks differ from the official NSE and BSE files, how they are revised after first publication, which sessions they miss, and how often seven trading signals change their answer under realistic data errors (simulation). No raw prices are included.

WHAT CHANGED FROM VERSION 1
- Data. v1 was exported on 20 Oct 2026; its simulation results came from an earlier study run (data through 16 Oct 2026, 20 repetitions per scenario), its row-level files ran to 19 Oct, and the paper quoted a still later run. v2 is pinned to data through 2026-10-19 and every file is cut at that date, so the dataset and the paper's numbers come from one state of the database.
- Simulation size. 200 repetitions of each simulated error scenario (v1: 20). Flip rates and their intervals are therefore based on ten times as many simulated errors and will differ somewhat from v1.
- Backtest sensitivity. The month-end backtest now uses 1,000,000 simulated histories per setting and 3 independent replicates (v1: 100 histories); backtest_sensitivity.csv reports each replicate and the spread between them (seed_spread_*), and the spread between replicates of the median and the 5th/95th percentiles is reported against a tolerance of Rs 2.0: largest spread over all three settings Rs 1.30.
- New files. nse_delivery_cost_schedule.csv (NSE delivery charges, STT, exchange, SEBI, stamp duty, GST, with sources and dates; 11.2 bp one way) and backtest_costs_19oct.csv (the rotation rule and benchmarks net of cost, 19 Oct 2026 sample).
- Datasheet. Signal definitions now include the exact RSI-zone rule (above 70 = +1, below 30 = -1, else 0) and the momentum-sign rule (sign of 20-session momentum, computed unrounded), which v1 did not define.
- Unchanged: file names and columns of the row-level files (revisions, settlement, three agreement files, missing sessions, official file log, universe, quality by group), the definitions of agreement (within one tick or 1 bp), live capture and scale mismatch, and the licence (CC BY 4.0).

Cite the repository and the paper. Code (MIT): https://github.com/Bhavex/market-intelligence-pg

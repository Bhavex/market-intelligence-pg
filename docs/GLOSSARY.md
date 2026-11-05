# Glossary

Simple explanations of the finance and database words used in the README and `docs/`. Terms are alphabetical within
each section.

## Finance and market terms

| Term | Plain meaning |
|---|---|
| **52-week high / low** | The highest / lowest price a stock reached in the last year (about 52 weeks). Used by some signals. |
| **Adjusted close** | The closing price rewritten for past splits, bonuses and dividends, so old and new prices can be compared fairly. |
| **Annualised** | A figure converted to a "per year" rate, so periods of different length can be compared. |
| **Backtest** | Replaying a trading rule on past data to see how it *would* have done. It is not a promise about the future. |
| **Basis point (bp)** | One hundredth of a percent: 100 bp = 1%. "11.2 bp costs" means 0.112% lost per trade. |
| **Benchmark** | The simple alternative a strategy must beat to be worth anything — here, equal-weight buy-and-hold. |
| **Bhavcopy** | NSE's official end-of-day file listing every stock's open, high, low, close and volume. Used to check Yahoo's numbers. Comes in a legacy and a newer "UDiFF" format. |
| **Bonus issue** | A company gives existing shareholders extra free shares (e.g. 1 for 1). Price per share falls, value does not. |
| **Bootstrap / block bootstrap** | Re-sampling past returns many times to estimate how uncertain a result is. "Block" keeps runs of consecutive days together so streaks are preserved. |
| **Buy-and-hold** | Buy the stocks once and keep them; no trading in between. |
| **CAGR** | Compound annual growth rate: the steady yearly return that would turn the start value into the end value. |
| **Confidence interval** | A range that probably contains the true value; wider means less certain. |
| **Corporate action** | Any company event that changes shares or price mechanics: split, bonus, dividend, etc. |
| **Dividend** | Cash a company pays to shareholders. The share price usually drops by about that amount on the ex-date. |
| **Drawdown** | How far a portfolio has fallen from its previous peak. "Max drawdown" is the worst such fall. |
| **End-of-day (EOD) data** | One price record per stock per trading day, published after the market closes; not live prices. |
| **Equal-weight** | Putting the same amount of money in each stock. |
| **Ex-date** | The first day a buyer of the stock no longer gets the upcoming dividend or bonus. |
| **Gross vs net return** | Gross is before trading costs; net is after them. |
| **Hit rate** | Share of signals or trades that turned out right / profitable. |
| **Holiday / trading session** | A session is a day the exchange is open. Holidays are weekdays when it is closed. |
| **ISIN** | A 12-character international code that uniquely identifies a security, even if its ticker changes. |
| **Large cap** | A company with a very large total market value; usually well known and heavily traded. |
| **Look-ahead bias** | Accidentally using information in a backtest that was not yet known on that date. It makes results look too good. |
| **Momentum** | The idea that stocks that rose recently tend to keep rising for a while. |
| **NSE** | National Stock Exchange of India, where the 25 stocks trade. Yahoo symbols end in `.NS`. |
| **Nifty** | NSE's family of stock indices (e.g. Nifty 50); used as reference baskets. |
| **OHLC / OHLCV** | Open, High, Low, Close (and Volume) for one stock on one day. |
| **Overfitting** | Tuning a strategy so closely to past data that it fails on new data. |
| **Point-in-time** | Data exactly as it was known on a past date, without later corrections. Needed for honest backtests. |
| **Precision / false-rejection rate** | For the data-quality checks: precision is how many flagged rows were truly bad; false-rejection rate is how many good rows were wrongly flagged. |
| **Cohen's κ (kappa)** | A score for how much two human labellers agree beyond chance; 1 = perfect, 0 = no better than luck. |
| **Rebalancing** | Trading to bring a portfolio back to its target weights. |
| **RSI** | Relative Strength Index: a 0–100 indicator of how strongly a price has been rising vs falling recently. Above ~70 is "overbought", below ~30 "oversold". |
| **Sector** | A group of companies in the same industry (e.g. banking, IT). |
| **Sector rotation** | A strategy that moves money into the sectors that have been doing best. |
| **Sharpe ratio** | Return per unit of risk (volatility). Higher is better; 0.89 beats 0.60. |
| **SMA (moving average)** | The average price over the last N days; smooths out daily noise. |
| **Split** | One share becomes several cheaper shares (e.g. 1 → 2). Value does not change. |
| **Stale price** | A price that has not updated when it should have — often a sign of missing data. |
| **STT / stamp duty** | Indian taxes charged on share trades; part of the trading costs in the backtest. |
| **Survivorship bias** | Testing only on companies that still exist today, ignoring ones that failed or were dropped. Makes results look better. |
| **Suspended** | Trading in a stock is stopped by the exchange, so it has no price that day. |
| **T+1** | Trades settle one business day after the trade date. |
| **Turnover** | How much of the portfolio is traded; high turnover means high costs. |
| **Universe** | The list of stocks a study or strategy is allowed to pick from, dated so it can change over time. |
| **Volatility** | How much a price jumps around; usually the standard deviation of returns. |
| **Z-score** | How many standard deviations a value is from the average; used to spot unusual moves. |

## Database and engineering terms

| Term | Plain meaning |
|---|---|
| **3NF / BCNF** | Normal forms: rules for splitting tables so each fact is stored once. BCNF (Boyce–Codd) is a stricter version of 3NF (third normal form). |
| **ACID** | Guarantees of a database transaction: all-or-nothing, consistent, isolated from others, durable once committed. |
| **Append-only** | A table where rows are only ever added, never changed or deleted, so history is kept. |
| **As-of query** | A query that asks "what did the database believe on date X?". |
| **Audit log / audit trigger** | A table that records who changed what and when, filled automatically by a trigger. |
| **B-tree / BRIN index** | Two kinds of index. B-tree is the general-purpose sorted index; BRIN is a tiny index that stores ranges, good for data that arrives in date order. |
| **Bitemporal** | Storing two timelines for each fact: when it was true in the market, and when the database learned it. |
| **CHECK constraint** | A rule the database enforces on each row, e.g. "price must be positive". |
| **Composite key** | A primary key made of more than one column, e.g. (ticker, trade date). |
| **CTE** | Common table expression: a named sub-query written with `WITH ...` to keep SQL readable. |
| **DDL / DML** | DDL changes the structure (CREATE, ALTER); DML changes the data (INSERT, UPDATE, DELETE). |
| **Entity / ER diagram** | An entity is a thing stored in the database (company, sector). An ER diagram draws entities and their relationships. |
| **ETL / pipeline** | The automated steps that fetch, check, load and process the data. |
| **EXPLAIN ANALYZE** | A PostgreSQL command that runs a query and shows how it was executed and how long each step took. |
| **Fencing / lease / heartbeat** | Safety for scheduled jobs: a job takes a lease (a lock with an expiry), keeps it alive with heartbeats, and its writes are rejected ("fenced") if it lost the lease. Stops two runs colliding. |
| **Fixture** | A small, known dataset used in tests where the right answer was calculated by hand. |
| **Foreign key (FK)** | A column that must match a row in another table, e.g. every price must belong to an existing company. |
| **Functional dependency** | "If I know A, I know B" (e.g. ticker → company name). Used to decide how to normalise tables. |
| **Idempotent** | Running the same step twice leaves the same result as running it once. |
| **Index** | An extra structure that lets the database find rows fast without reading the whole table. |
| **Index scan / seq scan** | Two ways to read a table: use an index to jump to rows, or read every row in order (sequential scan). |
| **Isolation** | How much running transactions can see of each other's unfinished work. |
| **Lag / lead (window functions)** | Look at the previous / next row's value, e.g. yesterday's close. |
| **Ledger** | A record of every movement of shares and cash, so the backtest's balance can be checked line by line. Also the "fetch ledger" of every data download. |
| **Least privilege / role** | A role is a database user or group. Least privilege means giving it only the permissions it needs (e.g. read-only for the API). |
| **Materialized view** | A saved query result stored like a table and refreshed on demand; much faster to read than recomputing. |
| **Median of N runs** | Timing each query several times and taking the middle value, so one slow run does not distort it. |
| **Natural key / surrogate key** | A natural key comes from real data (ticker); a surrogate key is an invented ID number. |
| **Neon** | The hosted (cloud) PostgreSQL service the production database runs on. |
| **`NOT VALID` → `VALIDATE`** | Add a constraint without checking old rows (fast, no downtime), then check them later in the background. |
| **Partition** | In window functions, `PARTITION BY` splits rows into groups (e.g. per ticker) before calculating. Also: splitting a big table into pieces. |
| **PL/pgSQL** | PostgreSQL's built-in programming language for functions and triggers that run inside the database. |
| **PostgreSQL** | The open-source relational database used by this project. |
| **Primary key** | The column(s) that uniquely identify each row. |
| **Row-level** | Applied to each row separately (e.g. a row-level trigger). |
| **Schema** | The design of the database: tables, columns, keys and constraints. |
| **Trigger** | Code the database runs automatically when a row is inserted, updated or deleted. |
| **TTL** | Time to live: how long a cached value is kept before it must be refreshed. |
| **UPSERT** | Insert a row, or update it if it already exists (`INSERT ... ON CONFLICT DO UPDATE`). |
| **VACUUM** | PostgreSQL housekeeping that cleans up old row versions and keeps statistics fresh. |
| **View** | A saved query you can select from like a table. It is recomputed each time unless materialized. |
| **Window function** | A SQL function that calculates across a "window" of related rows (e.g. a 20-day average) without collapsing them. |

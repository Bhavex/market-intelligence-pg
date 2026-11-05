# Related Work (draft)

> **Status: draft.** Every reference below is marked *verify before citing*. Titles, venues and years were written
> from memory and must be checked against the original source (and added to `references.md`) before this section
> goes into the paper. Where a detail was uncertain it has been left out rather than guessed.

This project sits at the meeting point of five lines of work: declarative data validation, constraint-based data
repair, temporal and bitemporal databases, reconciliation of financial market data, and the statistics of backtest
overfitting. We take ideas from each; our contribution is to combine them inside one PostgreSQL warehouse that is
small enough to inspect fully.

## 1. Data validation and data quality tools

Data quality has long been treated as multi-dimensional rather than as accuracy alone. Wang and Strong surveyed data
consumers and grouped quality into intrinsic, contextual, representational and accessibility dimensions
(Wang & Strong, "Beyond Accuracy: What Data Quality Means to Data Consumers", *Journal of Management Information
Systems*, 1996 — *verify before citing*).

More recent systems make validation declarative and run it automatically. Deequ, from Amazon, expresses checks as
"unit tests for data" computed as metrics over large datasets (Schelter et al., "Automating Large-Scale Data Quality
Verification", *PVLDB*, 2018 — *verify before citing*). Google's TensorFlow Data Validation checks incoming data
against an inferred schema in ML pipelines (Breck et al., "Data Validation for Machine Learning", *SysML/MLSys*,
2019 — *verify before citing*). Open-source tools such as Great Expectations and Pandera offer similar expectation-
or schema-based checks for Python dataframes (tool documentation — *verify before citing*).

These tools mostly run *outside* the database and give a pass/fail verdict for each batch. Our design differs in two
ways. First, most invariants are enforced by the database itself (CHECK, FK and key constraints, triggers). Second,
a failed or doubtful row is not silently dropped: it gets a timestamped quality state, so a later query can ask what
was known to be valid at a given time.

## 2. Data repair and inconsistent databases

The database community has studied how to repair data that violates integrity constraints. Bohannon et al. proposed
a cost-based model for repairing functional and inclusion dependency violations by changing values (Bohannon, Fan,
Flaster & Rastogi, *SIGMOD*, 2005 — *verify before citing*). Conditional functional dependencies extend FDs with
patterns that hold only on part of a relation (Fan, Geerts, Jia & Kementsietsidis, *ACM TODS*, 2008 — *verify before
citing*). Denial constraints generalise many of these forms and can be discovered from data (Chu, Ilyas & Papotti,
"Discovering Denial Constraints", *PVLDB*, 2013 — *verify before citing*). HoloClean frames repair as probabilistic
inference that combines constraints, external data and statistics (Rekatsinas, Chu, Ilyas & Ré, *PVLDB*, 2017 —
*verify before citing*). Ilyas and Chu give a book-length overview (*Data Cleaning*, ACM Books, 2019 — *verify
before citing*).

A different line avoids repair altogether. Consistent query answering returns only the answers that hold in every
minimal repair (Arenas, Bertossi & Chomicki, "Consistent Query Answers in Inconsistent Databases", *PODS*, 1999 —
*verify before citing*).

For backtesting, an automatic repair is risky. If a repair uses information that arrived later (for example a
corrected close price), it can leak the future into the past. We therefore keep the original observation, record
the correction as a new observation, and leave the choice of which version to use to an as-of query. This is closer
in spirit to consistent query answering than to in-place repair.

## 3. Temporal and bitemporal databases

Temporal databases separate *valid time* (when a fact was true in the world) from *transaction time* (when the
database recorded it); a bitemporal table keeps both. Snodgrass's book shows how to build such applications in
standard SQL (Snodgrass, *Developing Time-Oriented Database Applications in SQL*, Morgan Kaufmann, 1999 — *verify
before citing*). Date, Darwen and Lorentzos give a relational treatment of intervals and temporal operators
(*Temporal Data and the Relational Model*, Morgan Kaufmann, 2002 — *verify before citing*). SQL:2011 added
application-time and system-versioned tables to the standard (Kulkarni & Michels, "Temporal Features in SQL:2011",
*SIGMOD Record*, 2012 — *verify before citing*).

PostgreSQL does not implement SQL:2011 system versioning natively, so our bitemporal price history and append-only
observation table are built from ordinary tables, range or timestamp columns and triggers. The question we care
about is the transaction-time one: *what would the warehouse have returned on date D?* That question is what a
point-in-time backtest needs.

## 4. Market data reconciliation and point-in-time data

Errors and revisions in financial data are known to bias research. Shumway showed that missing delisting returns in
CRSP bias results (Shumway, "The Delisting Bias in CRSP Data", *Journal of Finance*, 1997 — *verify before
citing*). Survivorship bias, which comes from studying only the firms or funds that still exist, was measured for
mutual funds by Elton, Gruber and Blake (*Review of Financial Studies*, 1996 — *verify before citing*). Ljungqvist,
Malloy and Marston found that historical analyst recommendation data had been changed after the fact ("Rewriting
History", *Journal of Finance*, 2009 — *verify before citing*), which shows why vintage or point-in-time snapshots
matter.

In practice, firms reconcile vendor feeds against an authoritative source. For Indian equities, the exchange's
daily bhavcopy files from NSE are such a source. We know of no peer-reviewed study of reconciling free vendor data
against NSE bhavcopy files; this gap is one motivation for publishing our reconciliation procedure. *(Check this
claim with a literature search before submission.)* Our dated universe-membership table addresses survivorship bias
in the same way: the stock list is taken as of each date, not as of today.

## 5. Backtest overfitting and data snooping

Testing many strategy variants on the same history inflates apparent performance. White's Reality Check tests
whether the best of many models truly beats a benchmark (White, "A Reality Check for Data Snooping",
*Econometrica*, 2000 — *verify before citing*), and Hansen's test of superior predictive ability improves its power
(Hansen, *Journal of Business & Economic Statistics*, 2005 — *verify before citing*). Lo derived the sampling
distribution of the Sharpe ratio (Lo, "The Statistics of Sharpe Ratios", *Financial Analysts Journal*, 2002 —
*verify before citing*).

Bailey, Borwein, López de Prado and Zhu defined the probability of backtest overfitting and estimated it with
combinatorially symmetric cross-validation ("The Probability of Backtest Overfitting", *Journal of Computational
Finance*, 2017 — *verify before citing*). Bailey and López de Prado's deflated Sharpe ratio corrects for the number
of trials and for non-normal returns ("The Deflated Sharpe Ratio", *Journal of Portfolio Management*, 2014 — *verify
before citing*). Harvey, Liu and Zhu argued that, given how many factors have been tested, new factors should clear
a much higher t-statistic ("... and the Cross-Section of Expected Returns", *Review of Financial Studies*, 2016 —
*verify before citing*). López de Prado's book collects these methods for practitioners (*Advances in Financial
Machine Learning*, Wiley, 2018 — *verify before citing*).

Our backtest reports a negative result: neither signal beats equal-weight buy-and-hold after costs. These methods
matter for us mainly as a guard against the opposite failure. Any future search over signal parameters should
report the number of trials and a deflated or multiple-testing-adjusted Sharpe ratio, not only the best one.

## Positioning (summary)

| Area | Typical prior work | What this project adds |
| --- | --- | --- |
| Validation | External batch checks, pass/fail | In-database constraints plus timestamped quality states |
| Repair | In-place automatic repair | Append-only corrections, choice made at query time |
| Temporal | SQL:2011 or dedicated engines | Bitemporal history built in plain PostgreSQL |
| Reconciliation | Proprietary vendor processes | Open NSE bhavcopy reconciliation |
| Overfitting | Statistical corrections | Negative, cost-adjusted result with a lineage trail |

## Before this goes into the paper

- [ ] Check every reference (authors, title, venue, year, pages) against the source.
- [ ] Move checked references into `references.md` in the paper's citation style.
- [ ] Run a literature search for prior work on NSE / Indian market data quality and reconciliation.
- [ ] Remove the "verify before citing" markers only after each check.

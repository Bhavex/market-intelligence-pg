# Data rights: code vs. data

This repository contains two very different things, and they are covered by different terms.

> This page is a plain-language summary written by the project authors, not legal advice. If you plan any use beyond
> personal study or academic research, read the providers' own terms yourself or ask a lawyer.

## 1. The code — MIT licence

Everything **we wrote** is released under the [MIT licence](../LICENSE): Python (`src/`, `api/`, `app/`,
`marketintel/`, `scripts/`), SQL (`sql/`, `demo/`), tests, workflows, the static-site generator and the documentation.

You may use, copy, change and redistribute it, including commercially, as long as you keep the copyright notice and
licence text. It comes with no warranty.

**The MIT licence does not cover market data**, even when that data sits inside this repository or in a database built
by this code. We cannot grant rights we do not hold.

## 2. Market data — the provider's terms apply

### Yahoo Finance (prices, hourly bars, fundamentals)

- The pipeline fetches end-of-day prices, hourly fallback bars and fundamentals snapshots from Yahoo Finance through the
  unofficial `yfinance` library. Yahoo does not offer this as a public, licensed API.
- That data stays subject to Yahoo's own terms of service (and, through Yahoo, to the exchanges and vendors that supply
  it). Those terms generally allow personal, non-commercial use and restrict redistribution and commercial use.
- Yahoo can change, rate-limit or withdraw access at any time; the project already treats Yahoo as an unreliable source
  (see [DATA_LINEAGE.md](DATA_LINEAGE.md) and [TRUST.md](TRUST.md)).

### NSE (bhavcopy files, trading calendar, symbol lists)

- Official end-of-day "bhavcopy" files and related reference data come from the National Stock Exchange of India
  (NSE) website and are used for reconciliation against Yahoo.
- NSE data is subject to NSE's website terms of use. Downloading for personal reference is generally tolerated;
  systematic redistribution or commercial use of NSE data normally needs a licence from NSE (or its data vendor
  arm).
- Company names, ticker symbols and sector labels are facts used for identification. Trademarks belong to their owners.

## 3. What this means for files in this repository

| Item | Covered by |
|---|---|
| Source code, SQL, tests, workflows, docs | MIT licence |
| `app/sample_data/*.csv.gz`, `results/`, `site/` data files, any database dump or API response containing prices | Provider terms (Yahoo / NSE). Included for **educational demonstration only** |
| Numbers *derived by our code* (signals, backtest statistics, data-quality counts) | Our analysis, but derived from provider data; treat it as subject to the same restrictions when it reproduces prices |
| Seed list of 25 companies and sectors (`sql/02_seed.sql`) | MIT (it is our selection of public facts) |

## 4. Practical guidance

- **Studying, teaching, reproducing the paper:** fine. Run the pipeline yourself so you fetch the data under your own
  acceptance of the providers' terms.
- **Redistributing a price dataset** built with this code (e.g. publishing a CSV or a public database): check the
  Yahoo and NSE terms first; they likely forbid it without permission.
- **Commercial products:** you may reuse the code under MIT, but get market data from a licensed vendor.
- **Citing:** cite the software via [`CITATION.cff`](../CITATION.cff) / the Zenodo DOI; cite Yahoo Finance and NSE as
  data sources.

## 5. Disclaimer

The data and analytics are for education only and are not investment advice. Prices may be wrong, late or missing;
see [Known limitations](../README.md#known-limitations).

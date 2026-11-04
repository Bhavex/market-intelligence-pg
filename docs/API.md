# REST API

A read-only FastAPI service (`api/main.py`) over the same data source as the dashboard (`marketintel/datasource.py`):
- **with `DATABASE_URL`:** `SELECT`s on the reporting views, each inside a `SET TRANSACTION READ ONLY` transaction;
- **without it:** the bundled synthetic demo data.

```bash
pip install -r api/requirements.txt
uvicorn api.main:app --reload
```

Interactive OpenAPI docs are at **`/docs`**, the schema at `/openapi.json`.

## Endpoints

| Method & path | Query parameters | Returns |
|---|---|---|
| `GET /health` | | `{status, mode, latest_price_date}`; `status = degraded` if the database is unreachable |
| `GET /v1/sectors` | | the 5 sectors with company count and 1D/1W/1M/3M/YTD returns |
| `GET /v1/sectors/{sector}/companies` | | companies of one sector (name is case-insensitive) |
| `GET /v1/sectors/strength` | `on` **or** `start`/`end`, `page`, `page_size` | sector relative strength and rank; default = latest day |
| `GET /v1/companies` | `sector`, `active`, `page`, `page_size` | companies |
| `GET /v1/companies/{ticker}` | | one company plus its latest close, day change, trend, RSI and 52-week distance |
| `GET /v1/companies/{ticker}/prices` | `start`, `end`, `order=asc\|desc`, `page`, `page_size` | daily OHLCV |
| `GET /v1/companies/{ticker}/signals` | `start`, `end`, `order`, `anomalies_only`, `page`, `page_size` | SQL-computed signals per day |
| `GET /v1/pipeline/status` | | freshness, stale tickers, the last run of each job, the daily-update success rate over the last 30 runs |

Tickers are Yahoo symbols and case-insensitive (`infy.ns` works). Encode `&` in a URL: `M%26M.NS`.

## Pagination

List endpoints return an envelope:

```json
{"data": [...],
 "pagination": {"page": 2, "page_size": 100, "total": 770, "total_pages": 8, "has_next": true}}
```

`page` starts at 1. `page_size` runs from 1 to 500 and defaults to 100. In database mode the page is fetched with
`LIMIT/OFFSET` and the total with `COUNT(*)`. Both use the `(ticker, price_date)` primary key (see
`docs/PERFORMANCE.md`).

## Errors

Every error has the same shape:

```json
{"error": {"code": "invalid_request", "message": "One or more parameters are invalid.",
           "details": [{"field": "ticker", "message": "String should match pattern ...", "type": "string_pattern_mismatch"}]}}
```

| Status | `code` | When |
|---|---|---|
| 400 | `bad_request` | `start` after `end`, or `on` combined with `start`/`end` |
| 404 | `not_found` | unknown ticker, sector or route |
| 405 | `method_not_allowed` | any write method: the API is read-only |
| 422 | `invalid_request` | malformed ticker, bad date, `page < 1`, `page_size > 500`, unknown `order` |
| 503 | `data_unavailable` | the database is unreachable (no connection details are leaked) |

Successful `GET`s carry `Cache-Control: public, max-age=300`, and CORS is open for `GET` because the data is public and
read-only.

## Tests

`tests/test_api.py` has 34 tests. They run against the bundled demo data, so they need no database or network. One of
them simulates a database outage (503). When `TEST_DATABASE_URL` points at a throwaway database with sample data, one
more test runs every endpoint against PostgreSQL.

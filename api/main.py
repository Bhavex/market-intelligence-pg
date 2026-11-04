import math
import sys
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.schemas import (Company, CompanyDetail, ErrorResponse, Health, Paginated, Pagination, PipelineStatus, Price,
                         Sector, SectorStrength, Signal)
from marketintel.datasource import DataSource, DataUnavailable, get_source

API_VERSION = "1.0.0"
MAX_PAGE_SIZE = 500
TICKER_PATTERN = r"^[A-Za-z0-9&\-]{1,20}\.(NS|ns|BO|bo)$"
PRICE_COLUMNS = ["price_date", "open", "high", "low", "close", "adj_close", "volume"]
SIGNAL_COLUMNS = ["return_1d", "momentum_20d", "momentum_60d", "volatility_20d", "sma_50", "sma_200", "rsi_14",
                  "volume_zscore_20d", "trend", "is_anomaly", "anomaly_reason", "high_52w", "breakout_52w",
                  "pct_from_52w_high", "strong_breakout"]

ERRORS = {404: {"model": ErrorResponse}, 422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}}

app = FastAPI(
    title="NSE Sector Intelligence API",
    version=API_VERSION,
    description=("Read-only access to 25 NSE large caps across five sectors: prices, SQL-computed signals, sector "
                 "strength and pipeline status. Without DATABASE_URL the API serves bundled synthetic demo data."),
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def get_data() -> DataSource:
    if not hasattr(app.state, "source"):
        app.state.source = get_source()
    return app.state.source


Source = Annotated[DataSource, Depends(get_data)]
Ticker = Annotated[str, PathParam(pattern=TICKER_PATTERN, description="Yahoo symbol, e.g. INFY.NS (M&M.NS as M%26M.NS)")]
PageNo = Annotated[int, Query(ge=1, le=100_000, description="1-based page number")]
PageSize = Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE, description=f"rows per page, at most {MAX_PAGE_SIZE}")]
Start = Annotated[date | None, Query(description="first date, inclusive (YYYY-MM-DD)")]
End = Annotated[date | None, Query(description="last date, inclusive (YYYY-MM-DD)")]
Order = Annotated[Literal["asc", "desc"], Query(description="sort by date")]


def error(status: int, code: str, message: str, details=None) -> JSONResponse:
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e.get("loc", ()) if p not in ("query", "path")),
                "message": e.get("msg", ""), "type": e.get("type", "")} for e in exc.errors()]
    return error(422, "invalid_request", "One or more parameters are invalid.", details)


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    code = {400: "bad_request", 404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
    return error(exc.status_code, code, str(exc.detail))


@app.exception_handler(DataUnavailable)
async def data_unavailable(request: Request, exc: DataUnavailable):
    return error(503, "data_unavailable", "The database is not reachable right now. Try again shortly.")


@app.middleware("http")
async def cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.method == "GET" and response.status_code == 200:
        response.headers["Cache-Control"] = "public, max-age=300"
    return response


def clean(value):
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if value is pd.NaT or (not isinstance(value, (list, dict, str)) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if hasattr(value, "item"):
        return value.item()
    return value


def records(df: pd.DataFrame, columns=None) -> list[dict]:
    df = df[columns] if columns else df
    return [{k: clean(v) for k, v in row.items()} for row in df.to_dict("records")]


def paginate(items: list, total: int, page: int, page_size: int) -> dict:
    pages = max(1, math.ceil(total / page_size))
    return {"data": items, "pagination": Pagination(page=page, page_size=page_size, total=total, total_pages=pages,
                                                    has_next=page < pages)}


def check_range(start: date | None, end: date | None) -> None:
    if start and end and start > end:
        raise HTTPException(400, f"start ({start}) must be on or before end ({end})")


def require_company(src: DataSource, ticker: str) -> dict:
    company = src.company(ticker)
    if company is None:
        raise HTTPException(404, f"Unknown ticker {ticker.upper()}")
    return company


def require_sector(src: DataSource, sector: str) -> str:
    names = src.sectors()["sector_name"].tolist()
    match = next((n for n in names if n.lower() == sector.lower()), None)
    if match is None:
        raise HTTPException(404, f"Unknown sector {sector}. Known sectors: {', '.join(names)}")
    return match


@app.get("/health", response_model=Health, tags=["meta"])
def health(src: Source):
    try:
        _, hi = src.date_range()
        return Health(status="ok", mode=src.mode, latest_price_date=hi)
    except DataUnavailable:
        return Health(status="degraded", mode=src.mode)


@app.get("/v1/sectors", response_model=list[Sector], responses=ERRORS, tags=["sectors"])
def sectors(src: Source):
    s = src.sectors().merge(src.sector_returns(), on="sector_name", how="left")
    return records(s, [c for c in Sector.model_fields if c in s.columns])


@app.get("/v1/sectors/strength", response_model=Paginated[SectorStrength], responses=ERRORS, tags=["sectors"])
def sector_strength(src: Source, start: Start = None, end: End = None,
                    on: Annotated[date | None, Query(description="one day; default the latest day")] = None,
                    page: PageNo = 1, page_size: PageSize = 100):
    check_range(start, end)
    if on and (start or end):
        raise HTTPException(400, "use either 'on' or 'start'/'end', not both")
    if not (start or end or on):
        on = src.date_range()[1]
    df = src.sector_strength(on, on) if on else src.sector_strength(start, end)
    df = df.sort_values(["price_date", "strength_rank"], ascending=[False, True])
    total = len(df)
    chunk = df.iloc[(page - 1) * page_size: page * page_size]
    return paginate(records(chunk, list(SectorStrength.model_fields)), total, page, page_size)


@app.get("/v1/sectors/{sector}/companies", response_model=list[Company], responses=ERRORS, tags=["sectors"])
def sector_companies(src: Source, sector: Annotated[str, PathParam(min_length=1, max_length=50)]):
    name = require_sector(src, sector)
    return records(src.companies(name), list(Company.model_fields))


@app.get("/v1/companies", response_model=Paginated[Company], responses=ERRORS, tags=["companies"])
def companies(src: Source, sector: Annotated[str | None, Query(max_length=50)] = None,
              active: Annotated[bool | None, Query(description="filter by is_active")] = None,
              page: PageNo = 1, page_size: PageSize = 100):
    df = src.companies(require_sector(src, sector) if sector else None)
    if active is not None:
        df = df[df["is_active"] == active]
    chunk = df.iloc[(page - 1) * page_size: page * page_size]
    return paginate(records(chunk, list(Company.model_fields)), len(df), page, page_size)


@app.get("/v1/companies/{ticker}", response_model=CompanyDetail, responses=ERRORS, tags=["companies"])
def company(src: Source, ticker: Ticker):
    c = require_company(src, ticker)
    latest = src.latest_by_sector()
    row = latest[latest["ticker"] == c["ticker"]]
    extra = records(row, ["latest_date", "close", "change_pct", "trend", "rsi_14", "pct_from_52w_high"])
    return {**{k: clean(v) for k, v in c.items() if k in Company.model_fields}, **(extra[0] if extra else {})}


@app.get("/v1/companies/{ticker}/prices", response_model=Paginated[Price], responses=ERRORS, tags=["companies"])
def prices(src: Source, ticker: Ticker, start: Start = None, end: End = None, order: Order = "asc",
           page: PageNo = 1, page_size: PageSize = 100):
    check_range(start, end)
    c = require_company(src, ticker)
    p = src.company_page(c["ticker"], start, end, limit=page_size, offset=(page - 1) * page_size,
                         descending=order == "desc")
    return paginate(records(p.rows, PRICE_COLUMNS), p.total, page, page_size)


@app.get("/v1/companies/{ticker}/signals", response_model=Paginated[Signal], responses=ERRORS, tags=["companies"])
def signals(src: Source, ticker: Ticker, start: Start = None, end: End = None, order: Order = "asc",
            anomalies_only: Annotated[bool, Query(description="only rows flagged as anomalies")] = False,
            page: PageNo = 1, page_size: PageSize = 100):
    check_range(start, end)
    c = require_company(src, ticker)
    if anomalies_only:
        df = src.anomalies(start, end)
        df = df[df["ticker"] == c["ticker"]].sort_values("price_date", ascending=order == "asc")
        total, rows = len(df), df.iloc[(page - 1) * page_size: page * page_size]
    else:
        p = src.company_page(c["ticker"], start, end, limit=page_size, offset=(page - 1) * page_size,
                             descending=order == "desc")
        total, rows = p.total, p.rows
    rows = rows.rename(columns={"price_date": "signal_date"})
    return paginate(records(rows, ["signal_date"] + SIGNAL_COLUMNS), total, page, page_size)


@app.get("/v1/pipeline/status", response_model=PipelineStatus, responses=ERRORS, tags=["pipeline"])
def pipeline_status(src: Source):
    health_df = src.pipeline_health()
    fresh = src.freshness()
    runs = src.pipeline_runs(200)
    daily = runs[runs["job_name"] == "daily_update"].head(30)
    is_fresh = fresh["is_fresh"].fillna(False).astype(bool)
    jobs = records(health_df, [c for c in ("job_name", "status", "started_at", "finished_at", "rows_upserted", "message")
                               if c in health_df.columns])
    latest_jobs_ok = all(j["status"] in ("success", "running") for j in jobs if j["job_name"] == "daily_update")
    return PipelineStatus(
        mode=src.mode,
        healthy=bool(is_fresh.all()) and latest_jobs_ok,
        latest_price_date=clean(fresh["latest_price_date"].max()) if len(fresh) else None,
        expected_date=clean(fresh["expected_date"].max()) if len(fresh) else None,
        fresh_tickers=int(is_fresh.sum()),
        total_tickers=len(fresh),
        stale_tickers=fresh.loc[~is_fresh, "ticker"].tolist(),
        daily_update_success_rate_30=round(float((daily["status"] == "success").mean()), 4) if len(daily) else None,
        jobs=jobs,
    )

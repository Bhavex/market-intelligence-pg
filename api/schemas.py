from datetime import date, datetime
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Pagination(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int
    has_next: bool


class Paginated(BaseModel, Generic[T]):
    data: list[T]
    pagination: Pagination


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[dict] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    mode: Literal["database", "demo"]
    latest_price_date: date | None = None


class Sector(BaseModel):
    sector_name: str
    n_companies: int
    return_1d_pct: float | None = None
    return_1w_pct: float | None = None
    return_1m_pct: float | None = None
    return_3m_pct: float | None = None
    return_ytd_pct: float | None = None
    rank_1m: int | None = None


class Company(BaseModel):
    ticker: str
    company_name: str
    sector_name: str
    city_name: str | None = None
    state: str | None = None
    region: str | None = None
    is_active: bool


class CompanyDetail(Company):
    latest_date: date | None = None
    close: float | None = None
    change_pct: float | None = None
    trend: str | None = None
    rsi_14: float | None = None
    pct_from_52w_high: float | None = None


class Price(BaseModel):
    price_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float
    adj_close: float | None = None
    volume: int | None = None


class Signal(BaseModel):
    signal_date: date
    return_1d: float | None = None
    momentum_20d: float | None = None
    momentum_60d: float | None = None
    volatility_20d: float | None = None
    sma_50: float | None = None
    sma_200: float | None = None
    rsi_14: float | None = None
    volume_zscore_20d: float | None = None
    trend: str | None = None
    is_anomaly: bool | None = None
    anomaly_reason: str | None = None
    high_52w: float | None = None
    breakout_52w: bool | None = None
    pct_from_52w_high: float | None = None
    strong_breakout: bool | None = None


class SectorStrength(BaseModel):
    price_date: date
    sector_name: str
    avg_momentum_20d: float | None = None
    relative_strength: float | None = None
    strength_rank: int


class JobStatus(BaseModel):
    job_name: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    rows_upserted: int | None = None
    message: str | None = None


class PipelineStatus(BaseModel):
    mode: Literal["database", "demo"]
    healthy: bool
    latest_price_date: date | None = None
    expected_date: date | None = None
    fresh_tickers: int
    total_tickers: int
    stale_tickers: list[str] = Field(default_factory=list)
    daily_update_success_rate_30: float | None = None
    jobs: list[JobStatus]

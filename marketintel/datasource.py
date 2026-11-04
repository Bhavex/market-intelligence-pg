import os
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd

from marketintel import SAMPLE_DIR

COMPANY_DAILY_COLUMNS = [
    "price_date", "ticker", "company_name", "sector_name", "city_name", "state", "region",
    "open", "high", "low", "close", "adj_close", "volume", "return_1d", "momentum_20d", "momentum_60d",
    "volatility_20d", "sma_50", "sma_200", "rsi_14", "volume_zscore_20d", "trend", "is_anomaly",
    "anomaly_reason", "high_52w", "breakout_52w", "pct_from_52w_high", "strong_breakout",
]

EXPORTS = {
    "companies": """SELECT c.ticker, c.company_name, s.sector_name, ci.city_name, ci.state, ci.region,
                           ci.latitude, ci.longitude, c.is_active
                    FROM companies c JOIN sectors s ON s.sector_id = c.sector_id
                    LEFT JOIN cities ci ON ci.city_id = c.hq_city_id
                    WHERE c.in_dashboard ORDER BY s.sector_name, c.ticker""",
    "company_daily": f"""SELECT v.price_date, v.ticker, v.company_name, v.sector_name, v.city_name, v.state, v.region,
                                v.open, v.high, v.low, v.close, v.adj_close, v.volume, v.return_1d, v.momentum_20d,
                                v.momentum_60d, v.volatility_20d, v.sma_50, v.sma_200, v.rsi_14, v.volume_zscore_20d,
                                v.trend, v.is_anomaly, v.anomaly_reason, s.high_52w, s.breakout_52w,
                                s.pct_from_52w_high, s.strong_breakout
                         FROM v_company_daily v
                         LEFT JOIN signals s ON s.ticker = v.ticker AND s.signal_date = v.price_date""",
    "sector_daily": "SELECT * FROM v_sector_daily",
    "sector_performance": "SELECT * FROM v_sector_daily_performance",
    "sector_returns": "SELECT * FROM v_sector_returns_summary ORDER BY rank_1m",
    "sector_strength": "SELECT * FROM v_sector_strength",
    "latest_by_sector": "SELECT * FROM v_latest_prices_by_sector ORDER BY sector_name, rank_in_sector",
    "pipeline_runs": """SELECT run_id, job_name, started_at, finished_at, status, rows_upserted, tickers_failed, message
                        FROM pipeline_runs ORDER BY started_at DESC LIMIT 500""",
    "pipeline_health": "SELECT * FROM v_pipeline_health ORDER BY job_name",
    "freshness": "SELECT * FROM v_data_freshness ORDER BY ticker",
    "rejections": "SELECT * FROM v_rejections_summary ORDER BY rejected_on DESC, check_name",
}


class DataUnavailable(RuntimeError):
    pass


@dataclass
class Page:
    rows: pd.DataFrame
    total: int


def _date_filter(df: pd.DataFrame, col: str, start: date | None, end: date | None) -> pd.DataFrame:
    if start is not None:
        df = df[df[col] >= pd.Timestamp(start)]
    if end is not None:
        df = df[df[col] <= pd.Timestamp(end)]
    return df


class DataSource:
    mode = "base"

    def table(self, name: str) -> pd.DataFrame:
        raise NotImplementedError

    def companies(self, sector: str | None = None) -> pd.DataFrame:
        df = self.table("companies")
        if sector:
            df = df[df["sector_name"].str.lower() == sector.lower()]
        return df.reset_index(drop=True)

    def sectors(self) -> pd.DataFrame:
        c = self.table("companies")
        c = c[c["is_active"]]
        return (c.groupby("sector_name").agg(n_companies=("ticker", "count")).reset_index()
                .sort_values("sector_name").reset_index(drop=True))

    def company(self, ticker: str) -> dict | None:
        c = self.table("companies")
        hit = c[c["ticker"] == ticker.upper()]
        return None if hit.empty else hit.iloc[0].to_dict()

    def company_history(self, ticker: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        df = self.table("company_daily")
        df = df[df["ticker"] == ticker.upper()]
        return _date_filter(df, "price_date", start, end).sort_values("price_date").reset_index(drop=True)

    def company_page(self, ticker: str, start=None, end=None, limit=100, offset=0, descending=False) -> Page:
        df = self.company_history(ticker, start, end)
        if descending:
            df = df.iloc[::-1]
        return Page(df.iloc[offset:offset + limit].reset_index(drop=True), len(df))

    def sector_daily(self, start=None, end=None) -> pd.DataFrame:
        return _date_filter(self.table("sector_daily"), "price_date", start, end).sort_values(
            ["price_date", "sector_name"]).reset_index(drop=True)

    def sector_performance(self, start=None, end=None) -> pd.DataFrame:
        return _date_filter(self.table("sector_performance"), "price_date", start, end).sort_values(
            ["price_date", "sector_name"]).reset_index(drop=True)

    def sector_returns(self) -> pd.DataFrame:
        return self.table("sector_returns").reset_index(drop=True)

    def sector_strength(self, start=None, end=None) -> pd.DataFrame:
        return _date_filter(self.table("sector_strength"), "price_date", start, end).sort_values(
            ["price_date", "strength_rank"]).reset_index(drop=True)

    def latest_by_sector(self) -> pd.DataFrame:
        return self.table("latest_by_sector").reset_index(drop=True)

    def anomalies(self, start=None, end=None, sector=None, limit=None) -> pd.DataFrame:
        df = self.table("company_daily")
        df = df[df["is_anomaly"].fillna(False).astype(bool)]
        df = _date_filter(df, "price_date", start, end)
        if sector:
            df = df[df["sector_name"] == sector]
        df = df.sort_values(["price_date", "ticker"], ascending=[False, True])
        return (df.head(limit) if limit else df).reset_index(drop=True)

    def pipeline_runs(self, limit: int = 100) -> pd.DataFrame:
        return self.table("pipeline_runs").sort_values("started_at", ascending=False).head(limit).reset_index(drop=True)

    def pipeline_health(self) -> pd.DataFrame:
        return self.table("pipeline_health").reset_index(drop=True)

    def freshness(self) -> pd.DataFrame:
        return self.table("freshness").reset_index(drop=True)

    def rejections(self) -> pd.DataFrame:
        return self.table("rejections").reset_index(drop=True)

    def date_range(self) -> tuple:
        d = self.table("sector_daily")["price_date"]
        return d.min().date(), d.max().date()


def _parse(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if col in ("price_date", "rejected_on", "latest_date", "as_of", "latest_price_date", "expected_date",
                   "first_price_date", "last_price_date"):
            df[col] = pd.to_datetime(df[col])
        elif col in ("started_at", "finished_at"):
            df[col] = pd.to_datetime(df[col], utc=True, format="mixed")
    return df


class DemoSource(DataSource):
    mode = "demo"

    def __init__(self, directory: Path = SAMPLE_DIR):
        self.directory = Path(directory)
        self._cache = {}
        meta = self.directory / "ABOUT.txt"
        self.about = meta.read_text().strip() if meta.exists() else "bundled synthetic sample data"

    def table(self, name: str) -> pd.DataFrame:
        if name not in self._cache:
            path = self.directory / f"{name}.csv.gz"
            if not path.exists():
                raise DataUnavailable(f"sample file missing: {path.name}")
            df = pd.read_csv(path, keep_default_na=True)
            for col in ("is_anomaly", "breakout_52w", "strong_breakout", "is_active", "is_fresh", "is_current"):
                if col in df.columns:
                    df[col] = df[col].map({True: True, False: False, "True": True, "False": False, "t": True,
                                           "f": False}).astype("boolean")
            if "tickers_failed" in df.columns:
                df["tickers_failed"] = df["tickers_failed"].fillna("").map(
                    lambda s: [x for x in str(s).strip("{}[]").replace("'", "").split(",") if x.strip()])
            self._cache[name] = _parse(df)
        return self._cache[name].copy()


class DbSource(DataSource):
    mode = "database"

    def __init__(self, url: str, connect_timeout: int = 15):
        self.url = url
        self.connect_timeout = connect_timeout
        self._conn = None
        self._lock = threading.Lock()
        self.about = "live PostgreSQL (read-only queries on reporting views)"

    def _connection(self):
        import psycopg
        if self._conn is None or self._conn.closed or self._conn.broken:
            from psycopg.types.numeric import FloatLoader
            self._conn = psycopg.connect(self.url, connect_timeout=self.connect_timeout, autocommit=True)
            self._conn.adapters.register_loader("numeric", FloatLoader)
        return self._conn

    def query(self, sql: str, params=None) -> pd.DataFrame:
        import psycopg
        with self._lock:
            for attempt in (1, 2):
                try:
                    conn = self._connection()
                    with conn.transaction():
                        conn.execute("SET TRANSACTION READ ONLY")
                        cur = conn.execute(sql, params)
                        cols = [d.name for d in cur.description]
                        rows = cur.fetchall()
                    return _parse(pd.DataFrame(rows, columns=cols))
                except psycopg.OperationalError as exc:
                    self._conn = None
                    if attempt == 2:
                        raise DataUnavailable(f"database unavailable: {type(exc).__name__}") from exc

    def table(self, name: str) -> pd.DataFrame:
        return self.query(EXPORTS[name])

    def company_history(self, ticker, start=None, end=None):
        sql = EXPORTS["company_daily"] + " WHERE v.ticker = %s AND v.price_date >= %s AND v.price_date <= %s ORDER BY v.price_date"
        return self.query(sql, (ticker.upper(), start or date(1900, 1, 1), end or date(2999, 1, 1)))

    def company_page(self, ticker, start=None, end=None, limit=100, offset=0, descending=False):
        where = " WHERE v.ticker = %s AND v.price_date >= %s AND v.price_date <= %s"
        params = (ticker.upper(), start or date(1900, 1, 1), end or date(2999, 1, 1))
        total = int(self.query("SELECT COUNT(*) AS n FROM v_company_daily v" + where, params)["n"].iloc[0])
        order = "DESC" if descending else "ASC"
        rows = self.query(EXPORTS["company_daily"] + where + f" ORDER BY v.price_date {order} LIMIT %s OFFSET %s",
                          params + (limit, offset))
        return Page(rows, total)

    def sector_daily(self, start=None, end=None):
        return self.query("SELECT * FROM v_sector_daily WHERE price_date BETWEEN %s AND %s ORDER BY price_date, sector_name",
                          (start or date(1900, 1, 1), end or date(2999, 1, 1)))

    def sector_performance(self, start=None, end=None):
        return self.query("""SELECT * FROM v_sector_daily_performance WHERE price_date BETWEEN %s AND %s
                             ORDER BY price_date, sector_name""", (start or date(1900, 1, 1), end or date(2999, 1, 1)))

    def sector_strength(self, start=None, end=None):
        return self.query("""SELECT * FROM v_sector_strength WHERE price_date BETWEEN %s AND %s
                             ORDER BY price_date, strength_rank""", (start or date(1900, 1, 1), end or date(2999, 1, 1)))

    def anomalies(self, start=None, end=None, sector=None, limit=None):
        sql = EXPORTS["company_daily"] + " WHERE v.is_anomaly AND v.price_date BETWEEN %s AND %s"
        params = [start or date(1900, 1, 1), end or date(2999, 1, 1)]
        if sector:
            sql += " AND v.sector_name = %s"
            params.append(sector)
        sql += " ORDER BY v.price_date DESC, v.ticker"
        if limit:
            sql += " LIMIT %s"
            params.append(limit)
        return self.query(sql, tuple(params))

    def pipeline_runs(self, limit=100):
        return self.query("""SELECT run_id, job_name, started_at, finished_at, status, rows_upserted, tickers_failed, message
                             FROM pipeline_runs ORDER BY started_at DESC LIMIT %s""", (limit,))

    def date_range(self):
        r = self.query("SELECT MIN(price_date) AS lo, MAX(price_date) AS hi FROM daily_prices")
        return pd.Timestamp(r["lo"].iloc[0]).date(), pd.Timestamp(r["hi"].iloc[0]).date()


def get_source(url: str | None = None) -> DataSource:
    url = url if url is not None else os.environ.get("DATABASE_URL", "")
    return DbSource(url) if url else DemoSource()

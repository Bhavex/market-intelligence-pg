"""Exports dashboard data from the reporting views to site/data/*.json.
The public website only reads these static files - it never connects to the database.
Usage:  python src/export_site.py"""
import datetime as dt
import json
import math
from decimal import Decimal
from pathlib import Path

import pandas as pd

from db import get_conn

OUT = Path(__file__).resolve().parent.parent / "site" / "data"
SERIES_DAYS = 760          # ~2 years of daily points per stock for the stock explorer


def q(conn, sql, params=None):
    cur = conn.execute(sql, params)
    df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, Decimal)).any():
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def clean(v, nd=6):
    if v is None:
        return None
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        v = v.item()                                    # numpy -> python
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else round(v, nd)
    if isinstance(v, (dt.datetime, dt.date, pd.Timestamp)):
        return v.isoformat()[:10] if not isinstance(v, dt.datetime) else v.isoformat(timespec="minutes")
    return v


def records(df, nd=6):
    return [{k: clean(v, nd) for k, v in row.items()} for row in df.to_dict("records")]


def write(name, obj):
    (OUT / name).write_text(json.dumps(obj, separators=(",", ":")))
    print(f"wrote {name} ({(OUT / name).stat().st_size / 1024:.0f} KB)")


def backtest(conn):
    """Monthly sector rotation: hold the sector with the best 20-day momentum at the
    end of the previous month, vs. holding all 5 sectors equally. No costs, no slippage."""
    sd = q(conn, "SELECT price_date, sector_name, avg_return_1d, avg_momentum_20d FROM mv_sector_daily")
    sd["price_date"] = pd.to_datetime(sd["price_date"])
    ret = sd.pivot(index="price_date", columns="sector_name", values="avg_return_1d").sort_index().fillna(0.0)
    mom = sd.pivot(index="price_date", columns="sector_name", values="avg_momentum_20d").sort_index()

    period = ret.index.to_period("M")
    monthly = (1 + ret).groupby(period).prod() - 1
    month_end_mom = mom.groupby(mom.index.to_period("M")).last().dropna(how="all")
    pick = month_end_mom.idxmax(axis=1).shift(1).dropna()     # decided at the previous month-end

    months = [m for m in pick.index if m in monthly.index]
    strat = pd.Series([monthly.loc[m, pick[m]] for m in months], index=months)
    equal = monthly.loc[months].mean(axis=1)

    def stats(r):
        eq = (1 + r).cumprod()
        years = len(r) / 12
        vol = r.std() * math.sqrt(12)
        return {
            "total_return": float(eq.iloc[-1] - 1),
            "cagr": float(eq.iloc[-1] ** (1 / years) - 1) if years > 0 else None,
            "volatility": float(vol),
            "sharpe": float(r.mean() * 12 / vol) if vol > 0 else None,
            "max_drawdown": float((eq / eq.cummax() - 1).min()),
            "best_month": float(r.max()),
            "worst_month": float(r.min()),
        }

    curve = pd.DataFrame({
        "month": [str(m) for m in months],
        "strategy": (100 * (1 + strat).cumprod()).values,
        "equal": (100 * (1 + equal).cumprod()).values,
        "pick": [pick[m] for m in months],
    })
    return {
        "start": str(months[0]) if months else None,
        "end": str(months[-1]) if months else None,
        "months": len(months),
        "win_rate": float((strat > equal).mean()) if months else None,
        "strategy": {k: clean(v) for k, v in stats(strat).items()} if months else None,
        "equal": {k: clean(v) for k, v in stats(equal).items()} if months else None,
        "picks": {k: int(v) for k, v in pick.loc[months].value_counts().items()},
        "curve": records(curve, 3),
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        latest = conn.execute("SELECT MAX(price_date) FROM daily_prices").fetchone()[0]

        # ---------- summary ----------
        counts = conn.execute("""
            SELECT (SELECT COUNT(*) FROM daily_prices), (SELECT COUNT(*) FROM signals),
                   (SELECT MIN(price_date) FROM daily_prices),
                   (SELECT COUNT(*) FROM companies WHERE is_active),
                   (SELECT COUNT(DISTINCT started_at::date) FROM pipeline_runs
                     WHERE job_name = 'daily_update' AND status IN ('success','partial'))""").fetchone()
        snap = q(conn, "SELECT * FROM v_latest_snapshot")
        health = q(conn, "SELECT job_name, finished_at, status, rows_upserted, message FROM v_pipeline_health")
        write("summary.json", {
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes"),
            "latest_date": clean(latest),
            "first_date": clean(counts[2]),
            "price_rows": counts[0], "signal_rows": counts[1], "stocks": counts[3],
            "automated_days": counts[4],
            "n_up": int((snap["trend"] == "UP").sum()),
            "n_down": int((snap["trend"] == "DOWN").sum()),
            "n_anomalies": int((snap["is_anomaly"] & (snap["price_date"] == latest)).sum()),
            "pipeline": records(health),
        })

        # ---------- sectors ----------
        sd = q(conn, "SELECT price_date, sector_name, avg_return_1d FROM mv_sector_daily")
        sd["price_date"] = pd.to_datetime(sd["price_date"])
        ret = sd.pivot(index="price_date", columns="sector_name", values="avg_return_1d").sort_index().fillna(0.0)
        index = 100 * (1 + ret).cumprod()
        weekly = index.resample("W-FRI").last().dropna(how="all")
        yearly = (1 + ret).groupby(ret.index.year).prod() - 1
        strength = q(conn, """
            SELECT s.sector_name, s.strength_rank, s.avg_momentum_20d, s.relative_strength,
                   d.avg_volatility_20d, d.avg_rsi_14, d.n_uptrend, d.n_downtrend, d.n_stocks
            FROM v_sector_strength s
            JOIN mv_sector_daily d USING (price_date, sector_name)
            WHERE s.price_date = %s ORDER BY s.strength_rank""", (latest,))
        write("sectors.json", {
            "dates": [d.strftime("%Y-%m-%d") for d in weekly.index],
            "index": {c: [clean(v, 2) for v in weekly[c]] for c in weekly.columns},
            "yearly": {"years": [int(y) for y in yearly.index],
                       "returns": {c: [clean(v, 4) for v in yearly[c]] for c in yearly.columns}},
            "strength": records(strength, 4),
        })

        # ---------- stocks ----------
        fund = q(conn, """SELECT DISTINCT ON (ticker) ticker, market_cap, pe_ratio
                          FROM fundamentals ORDER BY ticker, snapshot_date DESC""")
        one_year = q(conn, """
            SELECT DISTINCT ON (ticker) ticker, close AS close_1y
            FROM daily_prices WHERE price_date <= %s::date - 365
            ORDER BY ticker, price_date DESC""", (latest,))
        snap = snap.merge(fund, on="ticker", how="left").merge(one_year, on="ticker", how="left")
        snap["return_1y"] = snap["close"] / snap["close_1y"] - 1
        cols = ["ticker", "company_name", "sector_name", "city_name", "state", "region", "latitude",
                "longitude", "close", "return_1d", "momentum_20d", "momentum_60d", "return_1y",
                "volatility_20d", "rsi_14", "trend", "is_anomaly", "market_cap", "pe_ratio"]
        hist = q(conn, """
            SELECT ticker, price_date, close, sma_50, sma_200, rsi_14, volume, is_anomaly
            FROM v_company_daily WHERE price_date >= %s::date - %s
            ORDER BY ticker, price_date""", (latest, SERIES_DAYS))
        series = {}
        for t, g in hist.groupby("ticker"):
            series[t] = {
                "d": [d.isoformat() for d in g["price_date"]],
                "c": [clean(v, 2) for v in g["close"]],
                "s50": [clean(v, 2) for v in g["sma_50"]],
                "s200": [clean(v, 2) for v in g["sma_200"]],
                "rsi": [clean(v, 1) for v in g["rsi_14"]],
                "a": [i for i, a in enumerate(g["is_anomaly"]) if a],
            }
        write("stocks.json", {"latest": records(snap[cols].sort_values("ticker"), 4), "series": series})

        # ---------- anomalies ----------
        anom = q(conn, """
            SELECT price_date, ticker, company_name, sector_name, close, return_1d,
                   volume_zscore_20d, anomaly_reason
            FROM v_anomalies WHERE price_date >= %s::date - 90
            ORDER BY price_date DESC, ABS(return_1d) DESC LIMIT 60""", (latest,))
        write("anomalies.json", records(anom, 4))

        # ---------- backtest ----------
        write("backtest.json", backtest(conn))


if __name__ == "__main__":
    main()

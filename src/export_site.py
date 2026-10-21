"""Exports dashboard data from the reporting views to site/data/*.json.
The public website only reads these static files - it never connects to the database.
Only DERIVED data is published: performance indexed to 100 at the start of each chart, signals, returns and
summary statistics. Raw closes, OHLC, volume and the exchanges' official prices are never written (the paper states
that raw prices are not redistributed); write() refuses any file that contains such a field.
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

# Field names that carry vendor/exchange prices or volumes. None may appear anywhere in a published file.
RAW_PRICE_FIELDS = frozenset({
    "open", "high", "low", "close", "adj_close", "volume", "close_1y", "old_close", "new_close", "old_volume",
    "new_volume", "yahoo_close", "official_close", "official_last_price", "best_close",
})


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


def assert_derived_only(obj, where="file"):
    """Raise if any dict key inside obj is a raw price/volume field."""
    if isinstance(obj, dict):
        bad = RAW_PRICE_FIELDS.intersection(obj)
        if bad:
            raise ValueError(f"{where}: raw price field(s) {sorted(bad)} must not be published")
        for k, v in obj.items():
            assert_derived_only(v, f"{where}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj[:50]):                 # rows share one schema; checking a sample is enough
            assert_derived_only(v, f"{where}[{i}]")


def index_series(hist):
    """Per-stock chart series indexed to 100 at the first close of the window; SMAs use the same base so the
    price/average relationship (and every crossover) looks exactly as before. Raw closes are not returned.
    Returns ({ticker: series dict}, {ticker: latest index level})."""
    series, level = {}, {}
    for t, g in hist.groupby("ticker"):
        closes = g["close"].dropna()
        if closes.empty or closes.iloc[0] == 0:
            continue
        base = float(closes.iloc[0])
        idx = lambda col: [clean(None if v is None or v != v else 100 * float(v) / base, 2) for v in g[col]]
        series[t] = {
            "d": [d.isoformat() for d in g["price_date"]],
            "c": idx("close"),
            "s50": idx("sma_50"),
            "s200": idx("sma_200"),
            "rsi": [clean(v, 1) for v in g["rsi_14"]],
            "a": [i for i, a in enumerate(g["is_anomaly"]) if a],
        }
        level[t] = series[t]["c"][-1]
    return series, level


def strip_trust(trust):
    """trust.json: keep the measurements, drop the price columns (incident example and disagreement list)."""
    for ex in trust.get("example") or []:
        for k in ("old_close", "new_close"):
            ex.pop(k, None)
    for row in trust.get("disagreements") or []:
        for k in ("yahoo_close", "official_close", "official_last_price"):
            row.pop(k, None)
    return trust


def write(name, obj):
    assert_derived_only(obj, name)
    (OUT / name).write_text(json.dumps(obj, separators=(",", ":")))
    print(f"wrote {name} ({(OUT / name).stat().st_size / 1024:.0f} KB)")


SECTOR_COLORS = {"IT": "#3987e5", "Banking": "#d95926", "Auto": "#199e70", "Pharma": "#c98500", "FMCG": "#d55181"}


def og_image(weekly, latest):
    """1200x630 social-share card (LinkedIn/WhatsApp preview), regenerated every day."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bg, ink, muted = "#1a1a19", "#f0efec", "#a5a49c"
    fig = plt.figure(figsize=(12, 6.3), dpi=100, facecolor=bg)
    fig.text(0.05, 0.86, "NSE Sector Intelligence", color=ink, fontsize=34, weight="bold")
    fig.text(0.05, 0.79, "Self-updating PostgreSQL database · 25 large caps · 5 sectors · signals computed in SQL",
             color=muted, fontsize=15)
    ax = fig.add_axes([0.05, 0.14, 0.72, 0.58], facecolor=bg)
    names = [n for n in ["IT", "Banking", "Auto", "Pharma", "FMCG"] if n in weekly]
    for name in names:
        ax.plot(weekly.index, weekly[name], color=SECTOR_COLORS[name], lw=2.4)
    # end-of-line labels, nudged apart so they never overlap
    lo, hi = float(weekly[names].min().min()), float(weekly[names].max().max())
    gap = (hi - lo) * 0.07
    ends = sorted((float(weekly[n].iloc[-1]), n) for n in names)
    ys = []
    for y, _ in ends:
        ys.append(max(y, ys[-1] + gap) if ys else y)
    for (y_end, name), y in zip(ends, ys):
        ax.annotate(name, (weekly.index[-1], y), xytext=(10, 0), textcoords="offset points",
                    color=SECTOR_COLORS[name], fontsize=13, va="center", weight="bold", annotation_clip=False)
    import matplotlib.dates as mdates
    ax.xaxis.set_major_locator(mdates.YearLocator(2 if len(weekly) > 300 else 1))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    ax.tick_params(colors=muted, labelsize=11)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#383835")
    ax.grid(axis="y", color="#2c2c2a", lw=1)
    fig.text(0.05, 0.05, f"Sector index since 2015 (start = 100) · data as of {latest:%d %b %Y}",
             color=muted, fontsize=12)
    fig.text(0.95, 0.05, "bhavex.github.io/market-intelligence-pg", color=ink, fontsize=12, ha="right")
    fig.savefig(OUT.parent / "og.png", facecolor=bg)
    plt.close(fig)
    print("wrote og.png")


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
                   (SELECT COUNT(*) FROM companies WHERE is_active AND in_dashboard),
                   (SELECT COUNT(DISTINCT started_at::date) FROM pipeline_runs
                     WHERE job_name = 'daily_update' AND status IN ('success','partial'))""").fetchone()
        snap = q(conn, "SELECT * FROM v_latest_snapshot")
        health = q(conn, "SELECT job_name, finished_at, status, rows_upserted, message FROM v_pipeline_health")
        # For the plain-English "Today in one minute" brief on the site
        sector_today = q(conn, """
            SELECT sector_name, avg_return_1d FROM v_sector_daily
            WHERE price_date = %s ORDER BY avg_return_1d DESC NULLS LAST""", (latest,))
        breakouts = q(conn, """
            SELECT s.ticker, c.company_name, sec.sector_name, s.strong_breakout AS strong
            FROM signals s
            JOIN companies c   ON c.ticker = s.ticker
            JOIN sectors   sec ON sec.sector_id = c.sector_id
            WHERE s.signal_date = %s AND s.breakout_52w AND c.in_dashboard
            ORDER BY s.strong_breakout DESC, s.ticker""", (latest,))
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
            "sector_today": records(sector_today, 4),
            "breakouts": records(breakouts),
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

        og_image(weekly, latest)

        # ---------- stocks ----------
        # Derived data only: returns and signals from the snapshot, and chart series indexed to 100 at the start
        # of the window. The latest close, market cap and P/E are not published (the page does not use the last two).
        one_year = q(conn, """
            SELECT DISTINCT ON (ticker) ticker, close AS close_1y
            FROM daily_prices WHERE price_date <= %s::date - 365
            ORDER BY ticker, price_date DESC""", (latest,))
        snap = snap.merge(one_year, on="ticker", how="left")
        snap["return_1y"] = snap["close"] / snap["close_1y"] - 1
        hist = q(conn, """
            SELECT ticker, price_date, close, sma_50, sma_200, rsi_14, volume, is_anomaly
            FROM v_company_daily WHERE price_date >= %s::date - %s
            ORDER BY ticker, price_date""", (latest, SERIES_DAYS))
        series, level = index_series(hist)
        snap["index_level"] = snap["ticker"].map(level)
        cols = ["ticker", "company_name", "sector_name", "city_name", "state", "region", "latitude",
                "longitude", "index_level", "return_1d", "momentum_20d", "momentum_60d", "return_1y",
                "volatility_20d", "rsi_14", "trend", "is_anomaly"]
        write("stocks.json", {"latest": records(snap[cols].sort_values("ticker"), 4), "series": series})

        # ---------- anomalies ----------
        anom = q(conn, """
            SELECT price_date, ticker, company_name, sector_name, return_1d,
                   volume_zscore_20d, anomaly_reason
            FROM v_anomalies WHERE price_date >= %s::date - 90
            ORDER BY price_date DESC, ABS(return_1d) DESC LIMIT 60""", (latest,))
        write("anomalies.json", records(anom, 4))

        # ---------- backtest ----------
        write("backtest.json", backtest(conn))

        # ---------- data trust: point-in-time history, official reconciliation, fragility ----------
        # Optional research layer (sql/08-10): if anything is missing the rest of the site still publishes.
        try:
            from trust_report import collect
            trust = strip_trust(collect(conn))
            assert_derived_only(trust, "trust.json")
            (OUT / "trust.json").write_text(json.dumps(trust, separators=(",", ":"), default=str))
            print("wrote trust.json")
        except Exception as exc:
            conn.rollback()
            print(f"trust.json skipped: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()

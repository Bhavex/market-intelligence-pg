import numpy as np
import pandas as pd

SYNTHETIC_NOTE = "SYNTHETIC SAMPLE DATA: generated in app/trust_data.py for demo mode. These numbers are not real measurements."

QUERIES = {
    "coverage": "SELECT year, status, pairs, pct_of_year, before_first_price, tickers, first_session, last_session "
                "FROM v_session_coverage_summary ORDER BY year NULLS FIRST, status",
    "quality": "SELECT date_trunc('week', rejected_at)::date AS week, 'rejected (validation)' AS state, COUNT(*) AS n_rows "
               "FROM rejected_prices GROUP BY 1 "
               "UNION ALL "
               "SELECT date_trunc('week', decided_at)::date, state, COUNT(*) FROM quality_states "
               "WHERE state IN ('suspect', 'rejected') GROUP BY 1, 2 ORDER BY 1, 2",
    "revision_summary": "SELECT sample, kind, field, n_differences, n_stock_days, n_tickers, mean_abs_diff_bp, "
                        "max_abs_diff_bp FROM v_revision_capture_summary ORDER BY sample, kind, field",
    "revision_rate": "SELECT sample, n_runs, rows_compared, rows_differing, pct_rows_differing "
                     "FROM v_revision_capture_rate ORDER BY sample",
    "reconciliation": "SELECT bhav_format, classification, n_stock_days, n_tickers, first_date, last_date, "
                      "mean_abs_diff_bp, max_abs_diff_bp FROM v_reconciliation_summary ORDER BY bhav_format, classification",
}

RECON_ORDER = ["match", "last_traded_not_close", "previous_session_close", "split_or_bonus_basis", "intraday_value",
               "unexplained", "missing_in_yahoo", "missing_in_bhavcopy"]


def is_read_only(sql: str) -> bool:
    s = sql.strip().lower()
    banned = ("insert ", "update ", "delete ", "drop ", "alter ", "create ", "truncate ", "grant ", ";")
    return (s.startswith("select") or s.startswith("with")) and not any(b in s for b in banned)


def from_database(src) -> dict:
    return {name: src.query(sql) for name, sql in QUERIES.items()}


def synthetic(seed: int = 7) -> dict:
    rng = np.random.default_rng(seed)
    statuses = ["present", "missing", "fetch_failed", "holiday", "suspended", "not_yet_published"]
    rows = []
    for year in (2023, 2024, 2025):
        counts = [6100 + int(rng.integers(0, 80)), int(rng.integers(5, 40)), int(rng.integers(0, 12)),
                  375, int(rng.integers(0, 6)), 0 if year < 2025 else 25]
        total = sum(counts)
        for status, n in zip(statuses, counts):
            rows.append(dict(year=year, status=status, pairs=n, pct_of_year=round(100 * n / total, 2),
                             before_first_price=0, tickers=25,
                             first_session=pd.Timestamp(f"{year}-01-01"), last_session=pd.Timestamp(f"{year}-12-31")))
    cov = pd.DataFrame(rows)
    tot = cov.groupby("status", as_index=False)["pairs"].sum()
    tot["pct_of_year"] = (100 * tot["pairs"] / tot["pairs"].sum()).round(2)
    tot = tot.assign(year=None, before_first_price=0, tickers=25, first_session=pd.Timestamp("2023-01-01"),
                     last_session=pd.Timestamp("2025-12-31"))
    cov = pd.concat([tot[cov.columns], cov], ignore_index=True)

    weeks = pd.date_range("2025-07-07", periods=16, freq="W-MON")
    q = []
    for state, lam in (("rejected (validation)", 1.2), ("suspect", 3.0), ("rejected", 0.6)):
        for w, n in zip(weeks, rng.poisson(lam, len(weeks))):
            if n:
                q.append(dict(week=w, state=state, n_rows=int(n)))
    quality = pd.DataFrame(q)

    revision_summary = pd.DataFrame([
        dict(sample="recent", kind="value_changed", field="adj_close", n_differences=42, n_stock_days=40, n_tickers=6,
             mean_abs_diff_bp=3.1, max_abs_diff_bp=18.4),
        dict(sample="recent", kind="value_changed", field="volume", n_differences=17, n_stock_days=17, n_tickers=5,
             mean_abs_diff_bp=55.0, max_abs_diff_bp=410.0),
        dict(sample="rotating", kind="value_changed", field="adj_close", n_differences=230, n_stock_days=115,
             n_tickers=3, mean_abs_diff_bp=95.2, max_abs_diff_bp=1210.0),
        dict(sample="rotating", kind="missing_in_feed", field=None, n_differences=4, n_stock_days=4, n_tickers=2,
             mean_abs_diff_bp=None, max_abs_diff_bp=None),
    ])
    revision_rate = pd.DataFrame([
        dict(sample="recent", n_runs=60, rows_compared=15000, rows_differing=59, pct_rows_differing=0.3933),
        dict(sample="rotating", n_runs=60, rows_compared=37500, rows_differing=234, pct_rows_differing=0.624),
    ])
    recon_counts = {"match": 5210, "last_traded_not_close": 61, "previous_session_close": 9,
                    "split_or_bonus_basis": 14, "intraday_value": 3, "unexplained": 7, "missing_in_yahoo": 5,
                    "missing_in_bhavcopy": 2}
    recon_bp = {"match": (0.4, 4.9), "last_traded_not_close": (12.5, 88.0), "previous_session_close": (140.0, 420.0),
                "split_or_bonus_basis": (4800.0, 6650.0), "intraday_value": (60.0, 150.0),
                "unexplained": (35.0, 260.0), "missing_in_yahoo": (None, None), "missing_in_bhavcopy": (None, None)}
    recon = pd.DataFrame([
        dict(bhav_format="udiff_cm", classification=k, n_stock_days=v, n_tickers=min(25, max(1, v // 3)),
             first_date=pd.Timestamp("2024-07-08"), last_date=pd.Timestamp("2025-10-31"),
             mean_abs_diff_bp=recon_bp[k][0], max_abs_diff_bp=recon_bp[k][1])
        for k, v in recon_counts.items()])
    return dict(coverage=cov, quality=quality, revision_summary=revision_summary, revision_rate=revision_rate,
                reconciliation=recon)


def load_trust(src) -> tuple:
    if getattr(src, "mode", "demo") == "database":
        return from_database(src), False
    return synthetic(), True


def coverage_totals(cov: pd.DataFrame) -> pd.DataFrame:
    return cov[cov["year"].isna()].sort_values("pairs", ascending=False)


def coverage_rate(cov: pd.DataFrame) -> float | None:
    t = coverage_totals(cov).set_index("status")["pairs"]
    denom = sum(t.get(s, 0) for s in ("present", "missing", "fetch_failed"))
    return None if not denom else 100.0 * t.get("present", 0) / denom


def reconciliation_by_category(recon: pd.DataFrame) -> pd.DataFrame:
    if recon.empty:
        return pd.DataFrame(columns=["classification", "n_stock_days", "share_pct", "mean_abs_diff_bp",
                                     "max_abs_diff_bp"])
    r = recon.copy()
    r["weighted"] = r["mean_abs_diff_bp"].astype(float) * r["n_stock_days"]
    g = r.groupby("classification", as_index=False).agg(n_stock_days=("n_stock_days", "sum"),
                                                         weighted=("weighted", "sum"),
                                                         max_abs_diff_bp=("max_abs_diff_bp", "max"))
    g["mean_abs_diff_bp"] = (g["weighted"] / g["n_stock_days"]).where(g["weighted"].notna() & (g["weighted"] != 0))
    g["share_pct"] = 100.0 * g["n_stock_days"] / g["n_stock_days"].sum()
    g["order"] = g["classification"].map({c: i for i, c in enumerate(RECON_ORDER)}).fillna(len(RECON_ORDER))
    return g.sort_values("order")[["classification", "n_stock_days", "share_pct", "mean_abs_diff_bp",
                                   "max_abs_diff_bp"]].reset_index(drop=True)


def revisions_changed(summary: pd.DataFrame) -> pd.DataFrame:
    return summary[summary["kind"] == "value_changed"].reset_index(drop=True)

import argparse
import math
import sys
from pathlib import Path

import pandas as pd

LOAD_SQL = """
SELECT ticker, trade_date, classification, yahoo_close, bhav_close, bhav_last, diff_bp
FROM v_reconciliation_latest
WHERE ticker IS NOT NULL AND classification NOT IN ('missing_in_yahoo', 'missing_in_bhavcopy')
"""
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def binom_tail(k: int, n: int, p: float) -> float:
    if k <= 0:
        return 1.0
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    return float(min(1.0, sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k, n + 1))))


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["trade_date"] = pd.to_datetime(d["trade_date"])
    for c in ("yahoo_close", "bhav_close", "bhav_last", "diff_bp"):
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d["disagree"] = d["classification"] != "match"
    d["diff_rs"] = d["yahoo_close"] - d["bhav_close"]
    d["diff_pct"] = 100 * d["diff_rs"] / d["bhav_close"]
    d["month"] = d["trade_date"].dt.strftime("%Y-%m")
    d["weekday"] = d["trade_date"].dt.day_name()
    return d


def rate_table(d: pd.DataFrame, key: str, p: float) -> pd.DataFrame:
    g = d.groupby(key).agg(pairs=("disagree", "size"), disagreements=("disagree", "sum"))
    g["rate_pct"] = 100 * g["disagreements"] / g["pairs"]
    g["p_value"] = [binom_tail(int(k), int(n), p) for k, n in zip(g["disagreements"], g["pairs"])]
    return g


def analyse(df: pd.DataFrame, alpha: float = 0.01) -> dict:
    d = prepare(df)
    if d.empty:
        return {"pairs": 0}
    n, k = len(d), int(d["disagree"].sum())
    p = k / n if n else 0.0
    bad = d[d["disagree"]]
    by_class = d.groupby("classification").agg(pairs=("disagree", "size"),
                                                mean_abs_rs=("diff_rs", lambda s: s.abs().mean()),
                                                max_abs_rs=("diff_rs", lambda s: s.abs().max()),
                                                mean_abs_pct=("diff_pct", lambda s: s.abs().mean()),
                                                max_abs_pct=("diff_pct", lambda s: s.abs().max()))
    by_class["share_pct"] = 100 * by_class["pairs"] / n
    by_ticker = rate_table(d, "ticker", p).sort_values(["disagreements", "rate_pct"], ascending=False)
    by_date = rate_table(d, "trade_date", p).sort_values(["disagreements"], ascending=False)
    by_month = rate_table(d, "month", p).sort_index()
    by_weekday = rate_table(d, "weekday", p).reindex([w for w in WEEKDAYS if w in set(d["weekday"])])
    tickers_tested, dates_tested = len(by_ticker), len(by_date)
    hot_tickers = by_ticker[(by_ticker["disagreements"] > 0) & (by_ticker["p_value"] < alpha / max(tickers_tested, 1))]
    hot_dates = by_date[(by_date["disagreements"] > 0) & (by_date["p_value"] < alpha / max(dates_tested, 1))]
    top5 = int(by_ticker["disagreements"].head(5).sum())
    return {"pairs": n, "disagreements": k, "rate_pct": 100 * p, "sessions": d["trade_date"].nunique(),
            "tickers": d["ticker"].nunique(), "first": d["trade_date"].min().date(), "last": d["trade_date"].max().date(),
            "by_class": by_class, "by_ticker": by_ticker, "by_date": by_date, "by_month": by_month,
            "by_weekday": by_weekday, "hot_tickers": hot_tickers, "hot_dates": hot_dates,
            "top5_share_pct": 100 * top5 / k if k else 0.0,
            "tickers_with_any": int((by_ticker["disagreements"] > 0).sum()), "alpha": alpha,
            "rows": bad.sort_values(["trade_date", "ticker"])}


def _table(df: pd.DataFrame, cols: list, index_name: str, limit: int | None = None, fmt=None) -> list:
    fmt = fmt or {}
    rows = df.head(limit) if limit else df
    out = [f"| {index_name} | " + " | ".join(cols) + " |", "|---|" + "---|" * len(cols)]
    for idx, r in rows.iterrows():
        label = idx.date() if isinstance(idx, pd.Timestamp) else idx
        out.append(f"| {label} | " + " | ".join(fmt.get(c, "{}").format(r[c]) for c in cols) + " |")
    return out


def markdown(a: dict, limit: int = 15) -> str:
    if not a.get("pairs"):
        return "### Reconciliation analysis\n\nNo stored reconciliation results.\n"
    f = {"rate_pct": "{:.2f}%", "p_value": "{:.3g}", "pairs": "{:.0f}", "disagreements": "{:.0f}",
         "share_pct": "{:.2f}%", "mean_abs_rs": "₹{:.2f}", "max_abs_rs": "₹{:.2f}", "mean_abs_pct": "{:.3f}%",
         "max_abs_pct": "{:.3f}%"}
    lines = ["### Reconciliation analysis (all stored results)", "",
             f"{a['pairs']} stock-days, {a['sessions']} sessions ({a['first']} to {a['last']}), {a['tickers']} tickers. "
             f"Disagreements: {a['disagreements']} ({a['rate_pct']:.2f}%). "
             f"{a['tickers_with_any']} tickers have at least one; the top 5 account for {a['top5_share_pct']:.0f}%.", "",
             "#### By category (size of Yahoo − NSE close)", ""]
    lines += _table(a["by_class"], ["pairs", "share_pct", "mean_abs_rs", "max_abs_rs", "mean_abs_pct", "max_abs_pct"],
                    "category", fmt=f)
    lines += ["", f"#### By ticker (top {limit})", ""]
    lines += _table(a["by_ticker"], ["pairs", "disagreements", "rate_pct", "p_value"], "ticker", limit, f)
    lines += ["", "#### By month", ""] + _table(a["by_month"], ["pairs", "disagreements", "rate_pct"], "month", fmt=f)
    lines += ["", "#### By weekday", ""] + _table(a["by_weekday"], ["pairs", "disagreements", "rate_pct"], "weekday", fmt=f)
    lines += ["", "#### Clustering", "",
              f"A ticker or date is flagged when its count is unlikely under the overall rate (one-sided binomial, "
              f"Bonferroni-corrected α = {a['alpha']:g}).", ""]
    hot = [f"- ticker {t}: {int(r.disagreements)} of {int(r.pairs)} (p = {r.p_value:.2g})" for t, r in a["hot_tickers"].iterrows()]
    hot += [f"- date {d.date()}: {int(r.disagreements)} of {int(r.pairs)} (p = {r.p_value:.2g})" for d, r in a["hot_dates"].iterrows()]
    lines += hot or ["- No ticker or date stands out at that level."]
    lines += ["", f"#### Disagreements (first {limit * 2})", "",
              "| date | ticker | category | Yahoo | NSE close | NSE last | diff ₹ | diff % |", "|---|---|---|---|---|---|---|---|"]
    for r in a["rows"].head(limit * 2).itertuples():
        lines.append(f"| {r.trade_date.date()} | {r.ticker} | {r.classification} | {r.yahoo_close:,.2f} | "
                     f"{r.bhav_close:,.2f} | {r.bhav_last:,.2f} | {r.diff_rs:+,.2f} | {r.diff_pct:+.3f}% |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from db import get_conn
    ap = argparse.ArgumentParser(description="Analyse every stored bhavcopy reconciliation result")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args(argv)
    with get_conn() as conn:
        cur = conn.execute(LOAD_SQL)
        df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
    a = analyse(df)
    text = markdown(a, args.limit)
    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "reconciliation_analysis.md").write_text(text)
        if a.get("pairs"):
            for name in ("by_class", "by_ticker", "by_month", "by_weekday", "by_date"):
                a[name].to_csv(args.out / f"reconciliation_{name}.csv")
    print(text)


if __name__ == "__main__":
    main()

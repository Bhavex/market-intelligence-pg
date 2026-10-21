"""Sector-rotation backtest for the paper revision: transaction costs, benchmarks, turnover.

Rebuilds the inputs of export_site.backtest() (mv_sector_daily.avg_return_1d / avg_momentum_20d) in pandas
from a daily-price CSV, with the same formulas as sql/03_signals.sql, then runs the same monthly rotation:
hold the sector with the best 20-day momentum at the previous month-end. No database access.

    python src/backtest_revision.py --prices raw.csv --out results/revision [--end 2026-10-13]

--prices is a CSV with columns ticker, sector, price_date, close, adj_close (raw vendor data: keep it out of git).

Cost model: each month the portfolio trades to its target weights; cost = bp * sum|dw| (every one-way notional
traded, buys and sells), taken off the start-of-month value. Switching sector sells 100% and buys 100%, so it costs
2 * bp; entering the first month costs 1 * bp for every series. Sharpe is mean*12 / (std*sqrt(12)) with a zero
risk-free rate, as in export_site.backtest().
"""
import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from india_costs import REALISTIC_BP

COSTS_BP = (0, 5, 10, REALISTIC_BP, 20, 30)
SCENARIO_DAY = "2026-10-13"

# 13 Oct 2026 closes stored by the manual run at 09:39 IST (intraday prices), CHANGES.md section 5.
STALE_13OCT = {
    "HCLTECH.NS": 1264.50, "TECHM.NS": 1496.50, "TCS.NS": 2203.70, "INFY.NS": 1032.05, "WIPRO.NS": 165.65,
    "HINDUNILVR.NS": 1850.60, "NESTLEIND.NS": 1351.30, "BAJAJ-AUTO.NS": 10192.00, "ICICIBANK.NS": 1345.70,
    "M&M.NS": 2833.80, "SBIN.NS": 948.00, "AXISBANK.NS": 1256.60, "CIPLA.NS": 1319.70, "MARUTI.NS": 11320.00,
    "DRREDDY.NS": 1232.00, "ITC.NS": 259.40, "DABUR.NS": 377.00, "HEROMOTOCO.NS": 4976.00, "KOTAKBANK.NS": 440.70,
    "EICHERMOT.NS": 7136.00, "BRITANNIA.NS": 4812.10, "LUPIN.NS": 1949.60, "SUNPHARMA.NS": 1767.50,
    "DIVISLAB.NS": 9297.50, "HDFCBANK.NS": 714.00,
}
SCENARIOS = {
    "clean": {},
    "contaminated_full_0939": STALE_13OCT,                    # production as of the 09:39 IST run (the paper's numbers)
    "contaminated_infy_only": {"INFY.NS": STALE_13OCT["INFY.NS"]},   # production after the fix run, INFY not healed
}


def with_overrides(prices: pd.DataFrame, closes: dict, day: str = SCENARIO_DAY) -> pd.DataFrame:
    """Replace the close of `day` for the given tickers and scale that day's adj_close by the same ratio.

    Scaling (not overwriting adj_close with the raw close) keeps the dividend adjustment consistent: a CSV pulled after a
    later ex-dividend date has every earlier adj_close rescaled (TCS Rs 12 on 14 Oct, HCLTECH Rs 12 on 16 Oct), but the
    stale 09:39 rows were stored before those dividends, so production's adj_close/close was 1 on `day`. Overwriting gave IT
    year-to-date -24.06% instead of the production -24.28%."""
    p = prices.copy()
    for ticker, close in closes.items():
        m = (p["ticker"] == ticker) & (p["price_date"] == pd.Timestamp(day))
        assert m.sum() == 1, f"{ticker} has {m.sum()} rows on {day}"
        ratio = close / p.loc[m, "close"].values
        p.loc[m, "adj_close"] = p.loc[m, "adj_close"].values * ratio
        p.loc[m, "close"] = close
    return p


def sector_daily(prices: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """avg_return_1d and avg_momentum_20d per sector and day (rows, not calendar days; rounded to 6 dp like `signals`)."""
    p = prices.sort_values(["ticker", "price_date"]).copy()
    p["px"] = p["adj_close"].fillna(p["close"])
    g = p.groupby("ticker")["px"]
    p["ret"] = (p["px"] / g.shift(1).replace(0, np.nan) - 1).round(6)
    p["mom"] = (p["px"] / g.shift(20).replace(0, np.nan) - 1).round(6)
    sd = p.groupby(["price_date", "sector"])[["ret", "mom"]].mean()          # AVG() skips NULLs
    return sd["ret"].unstack().sort_index(), sd["mom"].unstack().sort_index()


def monthly_inputs(ret: pd.DataFrame, mom: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    """Monthly sector returns and the sector held in each month (decided at the previous month-end)."""
    ret = ret.fillna(0.0)
    monthly = (1 + ret).groupby(ret.index.to_period("M")).prod() - 1
    month_end_mom = mom.groupby(mom.index.to_period("M")).last().dropna(how="all")
    pick = month_end_mom.idxmax(axis=1).shift(1).dropna()
    months = [m for m in pick.index if m in monthly.index]
    return monthly.loc[months], pick.loc[months]


def net_returns(gross: pd.Series, traded: pd.Series, cost_bp: float) -> pd.Series:
    """Cost is paid on the start-of-month value, before the month's return."""
    return (1 - cost_bp / 1e4 * traded) * (1 + gross) - 1


def series_gross_and_traded(monthly: pd.DataFrame, pick: pd.Series, stock_monthly: pd.DataFrame | None = None) -> dict:
    """{name: (gross monthly return, sum|dw| per month)} for the strategy and the benchmarks."""
    n = monthly.shape[1]
    entry_only = pd.Series([1.0] + [0.0] * (len(monthly) - 1), index=monthly.index)   # buy once, never trade

    switch = (pick != pick.shift(1)).astype(float) * 2.0
    switch.iloc[0] = 1.0                                           # first month: buy 100%
    strat = pd.Series([monthly.loc[m, pick[m]] for m in monthly.index], index=monthly.index)

    ew = monthly.mean(axis=1)                                      # rebalanced to 1/n every month
    drift = (1 + monthly).div(1 + ew, axis=0) / n                  # weights at month-end before rebalancing
    ew_traded = (1 / n - drift).abs().sum(axis=1).shift(1)
    ew_traded.iloc[0] = 1.0

    levels = (1 + monthly).cumprod().mean(axis=1)                  # buy and hold: never rebalanced
    bh = levels.pct_change()
    bh.iloc[0] = levels.iloc[0] - 1

    out = {"Rotation": (strat, switch), "Equal-weight, monthly rebalanced": (ew, ew_traded),
           "Equal-weight buy-and-hold (5 sectors)": (bh, entry_only)}
    if stock_monthly is not None:
        lv = (1 + stock_monthly).cumprod().mean(axis=1)
        bhs = lv.pct_change()
        bhs.iloc[0] = lv.iloc[0] - 1
        out["Equal-weight buy-and-hold (25 stocks)"] = (bhs, entry_only)
    return out


def stats(r: pd.Series, traded: pd.Series, ref: pd.Series | None = None) -> dict:
    eq = (1 + r).cumprod()
    years = len(r) / 12
    vol = r.std() * math.sqrt(12)
    peak = pd.concat([pd.Series([1.0]), eq.reset_index(drop=True)]).cummax().iloc[1:].values   # start capital counts
    return {
        "final_value": 100 * eq.iloc[-1],
        "total_return": eq.iloc[-1] - 1,
        "cagr": eq.iloc[-1] ** (1 / years) - 1,
        "volatility": vol,
        "sharpe": r.mean() * 12 / vol if vol > 0 else np.nan,
        "max_drawdown": (eq.values / peak - 1).min(),
        "turnover_per_year": traded.sum() / 2 / years,             # one-way: 1.0 = whole portfolio traded once a year
        "months_traded": float((traded > 1e-9).mean()),
        "win_rate_vs_ew": float((r > ref).mean()) if ref is not None else np.nan,
    }


def run_scenario(prices: pd.DataFrame, costs=COSTS_BP) -> tuple[pd.DataFrame, pd.DataFrame]:
    ret, mom = sector_daily(prices)
    monthly, pick = monthly_inputs(ret, mom)
    p = prices.sort_values(["ticker", "price_date"]).copy()
    p["px"] = p["adj_close"].fillna(p["close"])
    me = p.groupby([p["price_date"].dt.to_period("M"), "ticker"])["px"].last().unstack()
    stock_monthly = me.pct_change().loc[monthly.index]
    series = series_gross_and_traded(monthly, pick, stock_monthly)

    rows, path = [], {"month": [str(m) for m in monthly.index], "pick": pick.values}
    for bp in costs:
        ew_net = net_returns(*series["Equal-weight, monthly rebalanced"], bp)
        for name, (gross, traded) in series.items():
            net = net_returns(gross, traded, bp)
            s = stats(net, traded, ew_net if name == "Rotation" else None)
            rows.append({"series": name, "cost_bp": bp, **s})
            if bp == 0 or name == "Rotation":
                path[f"{name} | {bp}bp"] = net.values
    return pd.DataFrame(rows), pd.DataFrame(path)


def break_even_bp(prices: pd.DataFrame) -> float:
    """Cost per one-way notional at which rotation's final value equals the rebalanced equal-weight benchmark's, both net."""
    ret, mom = sector_daily(prices)
    monthly, pick = monthly_inputs(ret, mom)
    s = series_gross_and_traded(monthly, pick)
    f = lambda bp: ((1 + net_returns(*s["Rotation"], bp)).prod() -
                    (1 + net_returns(*s["Equal-weight, monthly rebalanced"], bp)).prod())
    lo, hi = 0.0, 500.0
    if f(lo) <= 0:
        return 0.0
    if f(hi) > 0:
        return float("inf")
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) > 0 else (lo, mid)
    return round(lo, 1)


def to_latex(df: pd.DataFrame, caption: str, label: str) -> str:
    cols = ["Series", "bp", "Final", "CAGR %", "Vol %", "Sharpe", "MaxDD %", "Turn/yr"]
    lines = [r"\begin{table}[ht]", r"\centering", r"\small", rf"\caption{{{caption}}}", rf"\label{{{label}}}",
             r"\begin{tabular}{lrrrrrrr}", r"\toprule", " & ".join(cols) + r" \\", r"\midrule"]
    for _, r in df.iterrows():
        lines.append(" & ".join([r["series"].replace("&", r"\&"), f"{r['cost_bp']:g}", f"{r['final_value']:.1f}",
                                 f"{100 * r['cagr']:.2f}", f"{100 * r['volatility']:.2f}", f"{r['sharpe']:.2f}",
                                 f"{100 * r['max_drawdown']:.1f}", f"{r['turnover_per_year']:.2f}"]) + r" \\")
    return "\n".join(lines + [r"\bottomrule", r"\end{tabular}", r"\end{table}", ""])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices", required=True, type=Path)
    ap.add_argument("--out", default=Path("results/revision"), type=Path)
    ap.add_argument("--end", default=SCENARIO_DAY, help="last session included (the paper's sample ends 2026-10-13)")
    a = ap.parse_args()

    prices = pd.read_csv(a.prices, parse_dates=["price_date"])
    prices = prices[prices["price_date"] <= pd.Timestamp(a.end)]
    a.out.mkdir(parents=True, exist_ok=True)

    summary, be = [], {}
    for name, closes in SCENARIOS.items():
        p = with_overrides(prices, closes)
        res, path = run_scenario(p)
        res.insert(0, "scenario", name)
        summary.append(res)
        path.to_csv(a.out / f"task6_monthly_{name}.csv", index=False, float_format="%.6f")
        be[name] = break_even_bp(p)
        print(f"{name}: {len(path)} months, break-even vs EW rebalanced = {be[name]} bp per one-way notional")
    summary = pd.concat(summary, ignore_index=True)
    summary.to_csv(a.out / "task6_backtest_summary.csv", index=False, float_format="%.6f")
    pd.Series(be, name="break_even_bp").to_csv(a.out / "task6_break_even_bp.csv")

    clean = summary[summary.scenario == "clean"]
    (a.out / "task6_backtest_clean.tex").write_text(to_latex(
        clean, "Sector rotation vs equal-weight benchmarks on the clean data, by transaction cost "
               "(bp per one-way notional traded; start = 100; turnover is one-way, per year).", "tab:backtest-costs"))
    zero = summary[(summary.cost_bp == 0) & (summary.series.isin(["Rotation", "Equal-weight, monthly rebalanced"]))]
    (a.out / "task6_backtest_contaminated.tex").write_text(to_latex(
        zero.assign(series=zero.scenario.str.replace("_", " ") + ": " + zero.series),
        "Effect of the 13 Oct 2026 intraday rows on the backtest (no costs).", "tab:backtest-contaminated"))
    print(summary[summary.cost_bp == 0].round(4).to_string(index=False))


if __name__ == "__main__":
    main()

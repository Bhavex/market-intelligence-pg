from datetime import date, timedelta

import pandas as pd
import pytest

import reconcile_report as RR


def frame():
    rows = []
    start = date(2026, 9, 1)
    days = [start + timedelta(days=i) for i in range(40) if (start + timedelta(days=i)).weekday() < 5]
    for d in days:
        for t in ("A.NS", "B.NS", "C.NS", "D.NS"):
            rows.append({"ticker": t, "trade_date": d, "classification": "match", "yahoo_close": 100.0,
                         "bhav_close": 100.0, "bhav_last": 100.0, "diff_bp": 0.0})
    for i, d in enumerate(days[:10]):
        rows[days.index(d) * 4] = {"ticker": "A.NS", "trade_date": d, "classification": "last_traded_not_close",
                                   "yahoo_close": 101.0, "bhav_close": 100.0, "bhav_last": 101.0, "diff_bp": 100.0}
    rows[-1] = {"ticker": "D.NS", "trade_date": days[-1], "classification": "intraday_value", "yahoo_close": 98.0,
                "bhav_close": 100.0, "bhav_last": 100.5, "diff_bp": -200.0}
    return pd.DataFrame(rows), days


def test_binomial_tail_by_hand():
    assert RR.binom_tail(0, 5, 0.1) == 1.0
    assert RR.binom_tail(5, 5, 0.5) == pytest.approx(1 / 32)
    assert RR.binom_tail(1, 2, 0.5) == pytest.approx(0.75)


def test_rates_sizes_and_clustering():
    df, days = frame()
    a = RR.analyse(df)
    assert a["pairs"] == 4 * len(days) and a["disagreements"] == 11
    assert a["rate_pct"] == pytest.approx(100 * 11 / (4 * len(days)))
    c = a["by_class"]
    assert c.loc["last_traded_not_close", "pairs"] == 10
    assert c.loc["last_traded_not_close", "mean_abs_rs"] == pytest.approx(1.0)
    assert c.loc["intraday_value", "max_abs_pct"] == pytest.approx(2.0)
    assert a["by_ticker"].index[0] == "A.NS" and a["by_ticker"].loc["A.NS", "disagreements"] == 10
    assert list(a["hot_tickers"].index) == ["A.NS"] and a["hot_dates"].empty
    assert a["tickers_with_any"] == 2 and a["top5_share_pct"] == pytest.approx(100)
    assert set(a["by_month"].index) == {"2026-09", "2026-10"}
    assert list(a["by_weekday"].index) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]


def test_markdown_has_every_section():
    df, _ = frame()
    md = RR.markdown(RR.analyse(df))
    for heading in ("By category", "By ticker", "By month", "By weekday", "Clustering", "Disagreements"):
        assert heading in md
    assert "ticker A.NS: 10 of" in md and "+1.00" in md and "-2.000%" in md
    assert "No stored" in RR.markdown(RR.analyse(df.head(0)))

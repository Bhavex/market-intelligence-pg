from datetime import date

import numpy as np
import pandas as pd
import pytest

import fragility as F
from conftest import weekdays


def frame(closes, volumes=None, ticker="INFY.NS", adj=None):
    n = len(closes)
    days = weekdays(date(2020, 1, 1), n)
    c = np.asarray(closes, float)
    return pd.DataFrame({
        "ticker": ticker, "price_date": days, "sector_name": "IT", "in_dashboard": True, "cap_group": "large",
        "open": c, "high": c * 1.01, "low": c * 0.99, "close": c,
        "adj_close": c if adj is None else np.asarray(adj, float),
        "volume": np.asarray(volumes if volumes is not None else [1000.0] * n, float),
    })


def signals(closes, volumes=None, **kw):
    f = F.features(frame(closes, volumes, **kw))
    return f, F.day_signals(f, f["px"].values, f["volume"].values)


class TestReturnsAndMomentum:
    def test_momentum_20d_is_the_20_session_return(self):
        closes = [100 + i for i in range(30)]
        f, s = signals(closes)
        assert np.isnan(s["mom20"][19])
        assert s["mom20"][20] == pytest.approx(120 / 100 - 1)
        assert s["mom20"][29] == pytest.approx(129 / 109 - 1)

    def test_momentum_sign(self):
        _, s = signals([100 - i * 0.5 for i in range(25)])
        assert s["momentum_sign"][-1] == -1

    def test_sma_matches_rolling_mean(self):
        closes = list(np.linspace(100, 200, 60))
        f, _ = signals(closes)
        assert f["sma50"].iloc[-1] == pytest.approx(np.mean(closes[-50:]))


class TestTrend:
    def test_neutral_until_200_sessions_exist(self):
        _, s = signals([100 + i for i in range(199)])
        assert set(s["trend"]) == {"NEUTRAL"}

    def test_uptrend_when_price_above_sma50_above_sma200(self):
        _, s = signals([100 + i for i in range(260)])
        assert s["trend"][-1] == "UP"

    def test_downtrend_when_price_below_sma50_below_sma200(self):
        _, s = signals([400 - i for i in range(260)])
        assert s["trend"][-1] == "DOWN"

    def test_mixed_is_neutral(self):
        closes = [100 + i for i in range(230)] + [330 - 3 * i for i in range(30)]
        _, s = signals(closes)
        assert s["trend"][-1] == "NEUTRAL"


class TestRsi:
    def test_only_gains_is_100(self):
        _, s = signals([100 + i for i in range(20)])
        assert s["rsi"][-1] == 100

    def test_flat_prices_are_50(self):
        _, s = signals([100.0] * 20)
        assert s["rsi"][-1] == 50

    def test_only_losses_is_0(self):
        _, s = signals([100 - i for i in range(20)])
        assert s["rsi"][-1] == pytest.approx(0)

    def test_matches_simple_average_formula(self):
        closes = [100, 102, 101, 104, 103, 107, 106, 105, 108, 110, 109, 111, 112, 110, 113]
        _, s = signals(closes)
        d = np.diff(closes)[-14:]
        gain, loss = d.clip(min=0).mean(), (-d).clip(min=0).mean()
        assert s["rsi"][-1] == pytest.approx(100 - 100 / (1 + gain / loss))

    def test_is_bounded(self):
        rng = np.random.default_rng(1)
        _, s = signals(list(100 * np.cumprod(1 + rng.normal(0, 0.02, 300))))
        assert np.nanmin(s["rsi"]) >= 0 and np.nanmax(s["rsi"]) <= 100


class TestAnomaly:
    def test_volume_spike_over_3_sigma(self):
        rng = np.random.default_rng(2)
        vols = list(1000 + rng.normal(0, 50, 40))
        vols[-1] = 5000
        closes = list(100 * np.cumprod(1 + rng.normal(0, 0.001, 40)))
        _, s = signals(closes, vols)
        assert bool(s["is_anomaly"][-1])
        assert not bool(s["is_anomaly"][-2])

    def test_price_spike_over_3_sigma_of_prior_20_days(self):
        rng = np.random.default_rng(3)
        closes = list(100 * np.cumprod(1 + rng.normal(0, 0.005, 40)))
        closes[-1] = closes[-2] * 1.10
        _, s = signals(closes)
        assert bool(s["is_anomaly"][-1])

    def test_calm_series_has_no_anomaly(self):
        rng = np.random.default_rng(4)
        _, s = signals(list(100 * np.cumprod(1 + rng.normal(0, 0.005, 40))), list(1000 + rng.normal(0, 10, 40)))
        assert not s["is_anomaly"][25:].any()


class TestBreakout52w:
    def test_null_until_252_prior_sessions(self):
        _, s = signals([100 + 0.1 * i for i in range(253)])
        assert np.isnan(s["breakout_52w"][251])
        assert s["breakout_52w"][252] == 1

    def test_breakout_only_when_strictly_above_previous_high(self):
        closes = [100.0] * 252 + [100.0, 100.5]
        _, s = signals(closes)
        assert s["breakout_52w"][252] == 0
        assert s["breakout_52w"][253] == 1

    def test_today_is_excluded_from_its_own_window(self):
        closes = [100.0] * 252 + [150.0]
        f, s = signals(closes)
        assert f["high52"].iloc[-1] == 100.0
        assert s["breakout_52w"][-1] == 1

    def test_strong_breakout_needs_volume_zscore_above_2(self):
        rng = np.random.default_rng(5)
        vols = list(1000 + rng.normal(0, 20, 254))
        vols[-1] = 3000
        closes = [100.0] * 252 + [99.0, 101.0]
        _, s = signals(closes, vols)
        assert s["strong_breakout"][-1] == 1
        vols[-1] = 1000
        _, s = signals(closes, vols)
        assert s["breakout_52w"][-1] == 1 and s["strong_breakout"][-1] == 0


class TestTickersAreIndependent:
    def test_windows_do_not_cross_tickers(self):
        a = frame([100 + i for i in range(30)], ticker="A.NS")
        b = frame([500 - i for i in range(30)], ticker="B.NS")
        f = F.features(pd.concat([a, b], ignore_index=True))
        s = F.day_signals(f, f["px"].values, f["volume"].values)
        first_b = int(np.flatnonzero(f["ticker"].values == "B.NS")[0])
        assert np.isnan(f["px_prev"].values[first_b])
        assert s["mom20"][first_b + 20] == pytest.approx(480 / 500 - 1)

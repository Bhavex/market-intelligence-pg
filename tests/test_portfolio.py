import pandas as pd
import pytest

import portfolio as P

D = pd.DatetimeIndex(["2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"])
D3 = D[:3]
NAN = float("nan")


def px(**cols):
    index = D if len(next(iter(cols.values()))) == 4 else D3
    return pd.DataFrame(cols, index=index, dtype=float)


def w(**weights):
    return pd.Series(weights, dtype=float)


def order(day, **weights):
    return P.Order(day, w(**weights))


def approx(values, rel=1e-9):
    return pytest.approx(list(values), rel=rel, abs=1e-9)


class TestRebalancing:
    def test_monthly_style_rebalance_back_to_equal_weights(self):
        prices = px(A=[10, 10, 20, 20], B=[10, 10, 10, 10])
        led = P.run(prices, [order(D[0], A=0.5, B=0.5), order(D[2], A=0.5, B=0.5)])
        assert led.nav.tolist() == approx([100, 100, 150, 150])
        assert led.shares.loc[D[1]].tolist() == approx([5, 5])
        assert led.shares.loc[D[3]].tolist() == approx([3.75, 7.5])
        assert led.turnover.tolist() == approx([0, 1.0, 0, 0.333333333333])
        assert led.returns.tolist() == approx([0, 0, 0.5, 0])
        assert led.trades[["ticker", "shares", "notional"]].values.tolist() == [
            ["A", 5.0, 50.0], ["B", 5.0, 50.0], ["A", -1.25, -25.0], ["B", 2.5, 25.0]]

    def test_dropping_a_name_sells_it_and_buys_the_rest(self):
        prices = px(A=[10, 10, 10, 10], B=[20, 20, 20, 20])
        led = P.run(prices, [order(D[0], A=0.5, B=0.5), order(D[1], B=1.0)])
        assert led.shares.loc[D[2]].tolist() == approx([0, 5])
        assert led.turnover.loc[D[2]] == pytest.approx(1.0)


class TestCosts:
    def test_one_percent_cost_on_the_buy_and_on_the_sell(self):
        prices = px(A=[10, 10, 10, 12])
        led = P.run(prices, [order(D[0], A=1.0), P.Order(D[2], w())], cost_bp=100)
        assert led.shares.loc[D[1], "A"] == pytest.approx(9.900990099)
        assert led.nav.tolist() == approx([100, 99.00990099, 99.00990099, 117.6237624], rel=1e-9)
        assert led.costs.tolist() == approx([0, 0.9900990099, 0, 1.188118812], rel=1e-9)
        assert led.costs.sum() == pytest.approx(2.178217822)
        assert led.turnover.tolist() == approx([0, 0.9900990099, 0, 1.0], rel=1e-9)
        assert led.cash.iloc[-1] == pytest.approx(117.6237624)

    def test_flat_prices_lose_only_the_fee(self):
        prices = px(A=[100, 100, 100, 100])
        led = P.run(prices, [order(D[0], A=1.0)], cost_bp=10)
        assert led.nav.iloc[-1] == pytest.approx(99.900099900)


class TestExecutionTiming:
    def test_next_session_close_is_the_default(self):
        prices = px(A=[10, 20, 20])
        led = P.run(prices, [order(D[0], A=1.0)])
        assert led.shares.loc[D3[1], "A"] == pytest.approx(5)
        assert led.nav.tolist() == approx([100, 100, 100])

    def test_same_close_execution_would_capture_the_jump(self):
        prices = px(A=[10, 20, 20])
        led = P.run(prices, [order(D[0], A=1.0)], lag=0)
        assert led.nav.tolist() == approx([100, 200, 200])

    def test_next_session_open(self):
        prices = px(A=[10, 20, 20])
        opens = px(A=[9, 15, 20])
        led = P.run(prices, [order(D[0], A=1.0)], exec_prices=opens)
        assert led.shares.loc[D3[1], "A"] == pytest.approx(6.666666667)
        assert led.nav.tolist() == approx([100, 133.3333333, 133.3333333], rel=1e-9)
        assert led.trades["price"].tolist() == [15.0]

    def test_signal_on_the_last_session_is_never_filled(self):
        prices = px(A=[10, 20, 20])
        led = P.run(prices, [order(D[2], A=1.0)])
        assert led.trades.empty and led.nav.tolist() == approx([100, 100, 100])


class TestSplitsAndBonuses:
    def test_two_for_one_split_on_unadjusted_prices(self):
        prices = px(A=[100, 100, 50, 55])
        led = P.run(prices, [order(D[0], A=1.0)], actions=[P.split("A", D[2], 10, 5)])
        assert led.shares["A"].tolist() == approx([0, 1, 2, 2])
        assert led.nav.tolist() == approx([100, 100, 100, 110])

    def test_ignoring_the_split_would_show_a_fake_halving(self):
        prices = px(A=[100, 100, 50, 55])
        led = P.run(prices, [order(D[0], A=1.0)])
        assert led.nav.tolist() == approx([100, 100, 50, 55])

    def test_split_dated_on_a_weekend_applies_on_the_next_session(self):
        prices = px(A=[100, 100, 50, 55])
        led = P.run(prices, [order(D[0], A=1.0)], actions=[P.split("A", "2024-01-06", 2, 1)])
        assert led.nav.tolist() == approx([100, 100, 100, 110])

    def test_split_adjusted_prices_must_not_be_split_again(self):
        prices = px(A=[50, 50, 50, 55])
        led = P.run(prices, [order(D[0], A=1.0)], actions=[P.split("A", D[2], 10, 5)], basis="split_adjusted")
        assert led.shares["A"].tolist() == approx([0, 2, 2, 2])
        assert led.nav.tolist() == approx([100, 100, 100, 110])

    def test_one_for_one_bonus_with_no_price_on_the_ex_date(self):
        prices = px(A=[100, 100, NAN, 52])
        led = P.run(prices, [order(D[0], A=1.0)], actions=[P.bonus("A", D[2], 1, 1)])
        assert led.shares["A"].tolist() == approx([0, 1, 2, 2])
        assert led.nav.tolist() == approx([100, 100, 100, 104])

    def test_three_for_two_bonus(self):
        prices = px(A=[90, 90, 60, 66])
        led = P.run(prices, [order(D[0], A=1.0)], actions=[P.bonus("A", D[2], 1, 2)])
        assert led.shares.loc[D[1], "A"] == pytest.approx(1.111111111)
        assert led.shares.loc[D[2], "A"] == pytest.approx(1.666666667)
        assert led.nav.tolist() == approx([100, 100, 100, 110], rel=1e-9)


class TestDividends:
    def test_cash_dividend_is_credited_on_the_ex_date(self):
        prices = px(A=[100, 100, 95, 96])
        led = P.run(prices, [order(D[0], A=1.0)], actions=[P.dividend("A", D[2], 5)])
        assert led.dividends.tolist() == approx([0, 0, 5, 0])
        assert led.cash.tolist() == approx([100, 0, 5, 5])
        assert led.nav.tolist() == approx([100, 100, 100, 101])

    def test_dividends_still_apply_on_split_adjusted_prices(self):
        prices = px(A=[100, 100, 95, 96])
        led = P.run(prices, [order(D[0], A=1.0)], actions=[P.dividend("A", D[2], 5)], basis="split_adjusted")
        assert led.nav.iloc[-1] == pytest.approx(101)

    def test_total_return_prices_refuse_explicit_actions(self):
        prices = px(A=[100, 100, 95, 96])
        with pytest.raises(ValueError, match="already include"):
            P.run(prices, [order(D[0], A=1.0)], actions=[P.dividend("A", D[2], 5)], basis="total_return")

    def test_dividend_before_buying_is_not_received(self):
        prices = px(A=[100, 95, 95, 96])
        led = P.run(prices, [order(D[0], A=1.0)], actions=[P.dividend("A", D[1], 5)])
        assert led.dividends.sum() == 0 and led.nav.iloc[-1] == pytest.approx(101.0526316)


class TestMissingPrices:
    def test_a_leg_with_no_price_waits_for_the_next_session(self):
        prices = px(A=[10, NAN, 10, 10], B=[10, 10, 10, 10])
        led = P.run(prices, [order(D[0], A=0.5, B=0.5)])
        assert led.trades[["date", "ticker", "shares"]].values.tolist() == [[D[1], "B", 5.0], [D[2], "A", 5.0]]
        assert led.cash.tolist() == approx([100, 50, 0, 0])
        assert led.turnover.tolist() == approx([0, 0.5, 0.5, 0])
        assert led.nav.tolist() == approx([100, 100, 100, 100])

    def test_a_sell_that_cannot_fill_blocks_the_buy_it_would_fund(self):
        prices = px(A=[10, 10, NAN, 10], B=[10, 10, 10, 10])
        led = P.run(prices, [order(D[0], A=1.0), order(D[1], B=1.0)])
        assert led.trades[["date", "ticker", "shares"]].values.tolist() == [
            [D[1], "A", 10.0], [D[3], "A", -10.0], [D[3], "B", 10.0]]
        assert led.shares.loc[D[2]].tolist() == approx([10, 0])
        assert led.cash.min() >= 0

    def test_held_stock_without_a_close_is_valued_at_its_last_close(self):
        prices = px(A=[10, 10, NAN, 12])
        led = P.run(prices, [order(D[0], A=1.0)])
        assert led.nav.tolist() == approx([100, 100, 100, 120])

    def test_an_order_gives_up_on_a_leg_after_max_open_sessions(self):
        prices = px(A=[10, 10, NAN, NAN], B=[10, 10, 10, 10])
        led = P.run(prices, [order(D[0], A=1.0), order(D[1], B=1.0)], max_open_sessions=1)
        assert led.trades["date"].tolist() == [D[1]]
        assert led.shares.loc[D[3]].tolist() == approx([10, 0])
        assert led.nav.tolist() == approx([100, 100, 100, 100])


class TestValidation:
    def test_weights_above_one_are_refused(self):
        with pytest.raises(ValueError, match="sum to at most 1"):
            P.run(px(A=[1, 1, 1, 1]), [order(D[0], A=1.2)])

    def test_unknown_basis_is_refused(self):
        with pytest.raises(ValueError, match="price basis"):
            P.run(px(A=[1, 1, 1, 1]), [], basis="adjusted")

    def test_callable_target_sees_current_weights(self):
        seen = []

        def keep(current):
            seen.append(current.round(6).to_dict())
            return current

        prices = px(A=[10, 10, 20, 20], B=[10, 10, 10, 10])
        led = P.run(prices, [order(D[0], A=0.5, B=0.5), P.Order(D[2], keep)])
        assert seen == [{"A": 0.666667, "B": 0.333333}]
        assert led.turnover.loc[D[3]] == pytest.approx(0)

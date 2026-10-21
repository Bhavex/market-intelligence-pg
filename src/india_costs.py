"""One-way cost of trading NSE equities for delivery, in basis points of the notional traded.

Schedule as published (fetched 21 Oct 2026; same sources in results/revision/zenodo_v2/nse_delivery_cost_schedule.csv):
  STT                 0.100% on the buy and on the sell (NSE "SEBI Turnover Fees, STT and Other levies", rates w.e.f. 1 Apr 2026)
  NSE transaction     Rs 307 per crore each side = 0.307 bp (NSE circular NSE/FA/73061, 27 Feb 2026, effective 1 Mar 2026)
  SEBI turnover fee   Rs 10 per crore each side = 0.1 bp (NSE page above)
  Stamp duty          0.015% on the buyer only (NSE page above)
  GST                 18% on brokerage + SEBI fee + exchange transaction charge (not on STT or stamp duty; Zerodha charges page)
  Brokerage           Rs 0 on delivery at Zerodha; other discount brokers charge a flat fee per order, see flat_fee_bp()
Not included: bid-ask spread and market impact, depository (DP) charge on sells, income tax (capital-gains tax).
"""
STT_BP = 10.0
NSE_TXN_BP = 0.307
SEBI_BP = 0.1
STAMP_BUY_BP = 1.5
GST_RATE = 0.18


def side_bp(buy: bool, brokerage_bp: float = 0.0) -> float:
    gst = GST_RATE * (brokerage_bp + SEBI_BP + NSE_TXN_BP)
    return STT_BP + NSE_TXN_BP + SEBI_BP + brokerage_bp + gst + (STAMP_BUY_BP if buy else 0.0)


def one_way_bp(brokerage_bp: float = 0.0) -> float:
    """Average of a buy and a sell: what the backtest charges per rupee traded (a sector switch sells and buys 100%)."""
    return (side_bp(True, brokerage_bp) + side_bp(False, brokerage_bp)) / 2


def flat_fee_bp(fee_rs: float, order_value_rs: float) -> float:
    """A flat per-order brokerage as bp of the order value."""
    return fee_rs / order_value_rs * 1e4


REALISTIC_BP = round(one_way_bp(), 1)       # 11.2 bp: zero-brokerage delivery, statutory and exchange charges only

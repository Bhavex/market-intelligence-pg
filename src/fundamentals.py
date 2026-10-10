"""Weekly snapshot of fundamentals (market cap, revenue, EPS, P/E, P/B, dividend yield).
Free Yahoo data only gives *current* values, so we store one dated snapshot per run —
history builds up week by week."""
import math
import time

import yfinance as yf

from db import active_tickers, get_conn, pipeline_run

UPSERT = """
INSERT INTO fundamentals (ticker, snapshot_date, market_cap, total_revenue, eps_ttm,
                          pe_ratio, price_to_book, dividend_yield)
VALUES (%s, CURRENT_DATE, %s, %s, %s, %s, %s, %s)
ON CONFLICT (ticker, snapshot_date) DO UPDATE SET
    market_cap = EXCLUDED.market_cap, total_revenue = EXCLUDED.total_revenue,
    eps_ttm = EXCLUDED.eps_ttm, pe_ratio = EXCLUDED.pe_ratio,
    price_to_book = EXCLUDED.price_to_book, dividend_yield = EXCLUDED.dividend_yield
"""


def _num(x):
    try:
        x = float(x)
        return None if math.isnan(x) or math.isinf(x) else x
    except (TypeError, ValueError):
        return None


def main():
    with get_conn() as conn:
        tickers = active_tickers(conn)
        with pipeline_run(conn, "fundamentals") as run:
            for t in tickers:
                try:
                    info = yf.Ticker(t).info
                    row = (t, _num(info.get("marketCap")), _num(info.get("totalRevenue")),
                           _num(info.get("trailingEps")), _num(info.get("trailingPE")),
                           _num(info.get("priceToBook")), _num(info.get("dividendYield")))
                    conn.execute(UPSERT, row)
                    conn.commit()
                    run["rows_upserted"] += 1
                    print(f"{t}: ok")
                except Exception as exc:
                    conn.rollback()
                    run["tickers_failed"].append(t)
                    print(f"{t}: FAILED ({exc})")
                time.sleep(0.5)  # be gentle with Yahoo


if __name__ == "__main__":
    main()

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import SECTOR_ORDER, header, load, pct, sector_color, sidebar_dates

header("Sector overview", "Equal-weighted performance of 25 NSE large caps across five sectors, updated after every close.")

with st.sidebar:
    start, end = sidebar_dates(365, key="overview_range")

returns = load("sector_returns")
latest = load("latest_by_sector")
perf = load("sector_performance", start, end)

as_of = pd.Timestamp(returns["as_of"].max()).date() if len(returns) else end
best = returns.sort_values("return_1m_pct", ascending=False).iloc[0] if len(returns) else None
adv = int(returns["advancers"].sum()) if len(returns) else 0
dec = int(returns["decliners"].sum()) if len(returns) else 0
breakouts = int(latest["breakout_52w"].fillna(False).astype(bool).sum())
anoms = int(latest["is_anomaly"].fillna(False).astype(bool).sum())

sectors_up = int((returns["return_1d_pct"] > 0).sum()) if len(returns) else 0
c = st.columns(5)
c[0].metric("Sectors up today", f"{sectors_up} / {len(returns)}", help=f"Latest session: {as_of:%d %b %Y}")
c[1].metric("Advancers / decliners", f"{adv} / {dec}", help="Stocks up / down on the latest session")
if best is not None:
    c[2].metric("Best sector (1M)", best["sector_name"], pct(best["return_1m_pct"]))
c[3].metric("New 52-week highs", breakouts, help="Stocks above the high of the previous 252 sessions")
c[4].metric("Anomalies today", anoms, help="Moves above 3σ of the prior 20 days, or volume z-score above 3")

st.subheader(f"Sector index · as of {as_of:%d %b %Y}")
st.caption("Each sector is the equal-weighted average daily return of its five stocks, compounded and rebased to 100 at the start of the period.")
fig = go.Figure()
for sector in SECTOR_ORDER:
    s = perf[perf["sector_name"] == sector].sort_values("price_date")
    if s.empty:
        continue
    idx = 100 * s["sector_index"] / s["sector_index"].iloc[0]
    fig.add_trace(go.Scatter(x=s["price_date"], y=idx, name=sector, mode="lines",
                             line=dict(width=2, color=sector_color(sector)),
                             hovertemplate="%{y:.1f}"))
fig.add_hline(y=100, line=dict(color="#c3c2b7", width=1, dash="dot"))
fig.update_layout(height=420, yaxis_title="Index (start = 100)")
st.plotly_chart(fig, use_container_width=True)

left, right = st.columns([1, 1.25])
with left:
    st.subheader("Returns scorecard")
    table = returns[["sector_name", "return_1d_pct", "return_1w_pct", "return_1m_pct", "return_3m_pct", "return_ytd_pct"]]
    table = table.rename(columns={"sector_name": "Sector", "return_1d_pct": "1D", "return_1w_pct": "1W",
                                  "return_1m_pct": "1M", "return_3m_pct": "3M", "return_ytd_pct": "YTD"})
    num = ["1D", "1W", "1M", "3M", "YTD"]
    lim = max(1.0, float(table[num].abs().max().max()))
    styled = (table.style.format({k: "{:+.2f}%" for k in num})
              .background_gradient(cmap="RdBu", subset=num, vmin=-lim, vmax=lim))
    st.dataframe(styled, hide_index=True, use_container_width=True)
    st.caption("Ranked by 1-month return. 1W = 5 sessions, 1M = 21, 3M = 63.")

with right:
    st.subheader("Latest prices")
    tabs = st.tabs(SECTOR_ORDER)
    for tab, sector in zip(tabs, SECTOR_ORDER):
        with tab:
            d = latest[latest["sector_name"] == sector][
                ["ticker", "company_name", "close", "change_pct", "trend", "rsi_14", "pct_from_52w_high"]]
            st.dataframe(
                d, hide_index=True, use_container_width=True,
                column_config={
                    "ticker": "Ticker", "company_name": "Company",
                    "close": st.column_config.NumberColumn("Close (₹)", format="%.2f"),
                    "change_pct": st.column_config.NumberColumn("Day %", format="%+.2f%%"),
                    "trend": "Trend",
                    "rsi_14": st.column_config.ProgressColumn("RSI 14", min_value=0, max_value=100, format="%.0f"),
                    "pct_from_52w_high": st.column_config.NumberColumn("vs 52W high", format="%+.1f%%"),
                })

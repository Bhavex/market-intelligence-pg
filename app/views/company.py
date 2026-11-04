import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from common import GRID, INK_2, SECTOR_ORDER, header, load, money, pct, sector_color, sidebar_dates

header("Company detail", "Price, moving averages, momentum, RSI and anomaly flags computed in SQL by refresh_signals().")

companies = load("companies")
with st.sidebar:
    sectors = [s for s in SECTOR_ORDER if s in set(companies["sector_name"])]
    sector = st.selectbox("Sector", sectors, key="company_sector")
    options = companies[(companies["sector_name"] == sector) & companies["is_active"].fillna(True).astype(bool)]
    labels = {r.ticker: f"{r.company_name} ({r.ticker})" for r in options.itertuples()}
    ticker = st.selectbox("Company", list(labels), format_func=labels.get, key="company_ticker")
    start, end = sidebar_dates(365, key="company_range")
    show_volume = st.toggle("Show volume", value=True)

hist = load("company_history", ticker, start, end)
if hist.empty:
    st.info("No price history in this period.")
    st.stop()

last = hist.iloc[-1]
color = sector_color(sector)
c = st.columns(6)
c[0].metric("Close", money(last["close"]), pct(100 * last["return_1d"]) if pd.notna(last["return_1d"]) else None)
c[1].metric("20D momentum", pct(100 * last["momentum_20d"]))
c[2].metric("Volatility (ann.)", "–" if pd.isna(last["volatility_20d"]) else f"{100 * last['volatility_20d']:.1f}%")
c[3].metric("RSI 14", "–" if pd.isna(last["rsi_14"]) else f"{last['rsi_14']:.0f}",
            help="Below 30 is often read as oversold, above 70 as overbought")
c[4].metric("Trend", {"UP": "▲ Up", "DOWN": "▼ Down"}.get(last["trend"], "● Neutral"),
            help="Up: price > SMA50 > SMA200. Down: price < SMA50 < SMA200.")
c[5].metric("vs 52-week high", pct(last["pct_from_52w_high"], 1))

rows = 3 if show_volume else 2
heights = [0.56, 0.2, 0.24] if show_volume else [0.68, 0.32]
titles = ["Price", "Volume", "RSI 14"] if show_volume else ["Price", "RSI 14"]
fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=heights,
                    subplot_titles=titles)
x = hist["price_date"]
fig.add_trace(go.Scatter(x=x, y=hist["close"], name="Close", line=dict(color=color, width=2),
                         hovertemplate="₹%{y:,.2f}"), row=1, col=1)
fig.add_trace(go.Scatter(x=x, y=hist["sma_50"], name="SMA 50", line=dict(color="#52514e", width=1.5),
                         hovertemplate="₹%{y:,.2f}"), row=1, col=1)
fig.add_trace(go.Scatter(x=x, y=hist["sma_200"], name="SMA 200", line=dict(color="#6250d6", width=1.5, dash="dash"),
                         hovertemplate="₹%{y:,.2f}"), row=1, col=1)
fig.add_trace(go.Scatter(x=x, y=hist["high_52w"], name="52-week high", line=dict(color="#c3c2b7", width=1, dash="dot"),
                         hovertemplate="₹%{y:,.2f}"), row=1, col=1)
anom = hist[hist["is_anomaly"].fillna(False).astype(bool)]
if len(anom):
    fig.add_trace(go.Scatter(x=anom["price_date"], y=anom["close"], name="Anomaly", mode="markers",
                             marker=dict(symbol="diamond", size=9, color="#e34948", line=dict(color="white", width=2)),
                             customdata=anom["anomaly_reason"], hovertemplate="%{customdata}"), row=1, col=1)
brk = hist[hist["breakout_52w"].fillna(False).astype(bool)]
if len(brk):
    fig.add_trace(go.Scatter(x=brk["price_date"], y=brk["close"], name="52-week breakout", mode="markers",
                             marker=dict(symbol="triangle-up", size=9, color="#1baf7a", line=dict(color="white", width=2)),
                             hovertemplate="breakout"), row=1, col=1)
rsi_row = 2
if show_volume:
    fig.add_trace(go.Bar(x=x, y=hist["volume"], name="Volume", marker=dict(color=color, opacity=0.45),
                         showlegend=False, hovertemplate="%{y:,.0f}"), row=2, col=1)
    rsi_row = 3
fig.add_hrect(y0=30, y1=70, fillcolor="#f0efec", line_width=0, row=rsi_row, col=1)
fig.add_trace(go.Scatter(x=x, y=hist["rsi_14"], name="RSI 14", line=dict(color=color, width=1.5),
                         showlegend=False, hovertemplate="%{y:.0f}"), row=rsi_row, col=1)
fig.update_yaxes(range=[0, 100], tickvals=[30, 50, 70], row=rsi_row, col=1)
fig.update_yaxes(tickprefix="₹", row=1, col=1)
fig.update_annotations(font=dict(size=12, color=INK_2), x=0, xanchor="left")
fig.update_layout(height=720 if show_volume else 600, bargap=0.1)
st.plotly_chart(fig, use_container_width=True)

st.subheader("Momentum")
mom = go.Figure()
mom.add_trace(go.Scatter(x=x, y=100 * hist["momentum_20d"], name="20-day", line=dict(color=color, width=2),
                         hovertemplate="%{y:+.1f}%"))
mom.add_trace(go.Scatter(x=x, y=100 * hist["momentum_60d"], name="60-day", line=dict(color="#52514e", width=1.5),
                         hovertemplate="%{y:+.1f}%"))
mom.add_hline(y=0, line=dict(color=GRID, width=1))
mom.update_layout(height=280, yaxis_ticksuffix="%")
st.plotly_chart(mom, use_container_width=True)

with st.expander("Data table"):
    cols = ["price_date", "open", "high", "low", "close", "volume", "return_1d", "momentum_20d", "sma_50",
            "sma_200", "rsi_14", "volume_zscore_20d", "trend", "is_anomaly", "anomaly_reason", "breakout_52w"]
    st.dataframe(hist[cols].iloc[::-1], hide_index=True, use_container_width=True,
                 column_config={"price_date": st.column_config.DateColumn("Date"),
                                "return_1d": st.column_config.NumberColumn("Return 1D", format="percent"),
                                "momentum_20d": st.column_config.NumberColumn("Mom 20D", format="percent")})
    st.download_button("Download CSV", hist[cols].to_csv(index=False).encode(), f"{ticker}.csv", "text/csv")

import plotly.graph_objects as go
import streamlit as st

from common import SECTOR_ORDER, header, load, sector_color, sidebar_dates

header("Anomalies", "Sessions where a stock moved more than 3σ of its previous 20 days, or volume was more than 3σ above normal.")

with st.sidebar:
    start, end = sidebar_dates(183, key="anomaly_range")
    sectors = st.multiselect("Sectors", SECTOR_ORDER, default=SECTOR_ORDER, key="anomaly_sectors")
    reasons = st.multiselect("Type", ["price_spike", "volume_spike", "price_and_volume_spike"],
                             default=["price_spike", "volume_spike", "price_and_volume_spike"], key="anomaly_types")

df = load("anomalies", start, end)
df = df[df["sector_name"].isin(sectors) & df["anomaly_reason"].isin(reasons)]

c = st.columns(4)
c[0].metric("Anomalies", len(df))
c[1].metric("Price spikes", int(df["anomaly_reason"].str.contains("price").sum()))
c[2].metric("Volume spikes", int(df["anomaly_reason"].str.contains("volume").sum()))
c[3].metric("Stocks affected", df["ticker"].nunique())

if df.empty:
    st.info("No anomalies match these filters.")
    st.stop()

st.subheader("Timeline")
st.caption("Each mark is one stock-session; height is that day's return. Hover for details.")
fig = go.Figure()
for sector in SECTOR_ORDER:
    s = df[df["sector_name"] == sector]
    if s.empty:
        continue
    fig.add_trace(go.Scatter(
        x=s["price_date"], y=100 * s["return_1d"], mode="markers", name=sector,
        marker=dict(size=9, color=sector_color(sector), line=dict(color="white", width=2),
                    symbol=["diamond" if "price" in r else "circle" for r in s["anomaly_reason"]]),
        customdata=s[["ticker", "anomaly_reason", "volume_zscore_20d"]].values,
        hovertemplate="%{customdata[0]}<br>%{customdata[1]}<br>return %{y:+.2f}%<br>volume z %{customdata[2]:.1f}<extra></extra>"))
fig.add_hline(y=0, line=dict(color="#c3c2b7", width=1))
fig.update_layout(height=380, yaxis_ticksuffix="%", hovermode="closest")
st.plotly_chart(fig, use_container_width=True)
st.caption("◆ price spike (or both) · ● volume spike only")

left, right = st.columns([1, 1.6])
with left:
    st.subheader("By stock")
    counts = df.groupby(["ticker", "sector_name"]).size().reset_index(name="n").sort_values("n", ascending=False).head(12)
    bar = go.Figure(go.Bar(x=counts["n"], y=counts["ticker"], orientation="h",
                           marker=dict(color=[sector_color(s) for s in counts["sector_name"]], cornerradius=4),
                           hovertemplate="%{y}: %{x}<extra></extra>"))
    bar.update_layout(height=380, yaxis=dict(autorange="reversed"), hovermode="closest")
    st.plotly_chart(bar, use_container_width=True)
with right:
    st.subheader("All anomalies")
    st.dataframe(
        df[["price_date", "ticker", "company_name", "sector_name", "close", "return_1d", "volume_zscore_20d", "anomaly_reason"]],
        hide_index=True, use_container_width=True, height=380,
        column_config={"price_date": st.column_config.DateColumn("Date"), "ticker": "Ticker", "company_name": "Company",
                       "sector_name": "Sector", "close": st.column_config.NumberColumn("Close (₹)", format="%.2f"),
                       "return_1d": st.column_config.NumberColumn("Return", format="percent"),
                       "volume_zscore_20d": st.column_config.NumberColumn("Volume z", format="%.1f"),
                       "anomaly_reason": "Type"})

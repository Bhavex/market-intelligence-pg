import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import DIVERGING, SECTOR_ORDER, header, load, sector_color, sidebar_dates

header("Sector strength", "Each sector's average 20-day momentum relative to the whole universe, ranked every session (v_sector_strength).")

with st.sidebar:
    start, end = sidebar_dates(183, key="strength_range")

ss = load("sector_strength", start, end)
if ss.empty:
    st.info("No data in this period.")
    st.stop()

latest_day = ss["price_date"].max()
now = ss[ss["price_date"] == latest_day].sort_values("strength_rank")
leader = now.iloc[0]
c = st.columns(3)
c[0].metric("Strongest sector", leader["sector_name"], f"{100 * leader['relative_strength']:+.2f} pp vs universe")
c[1].metric("Weakest sector", now.iloc[-1]["sector_name"], f"{100 * now.iloc[-1]['relative_strength']:+.2f} pp",
            delta_color="normal")
leaders = ss[ss["strength_rank"] == 1].sort_values("price_date")
switches = int((leaders["sector_name"] != leaders["sector_name"].shift()).sum() - 1)
c[2].metric("Leadership changes", max(switches, 0), help="Times the rank-1 sector changed in the period")

left, right = st.columns([1, 1.6])
with left:
    st.subheader(f"Ranking on {pd.Timestamp(latest_day):%d %b %Y}")
    labels = [f"{s}  {100 * v:+.2f} pp" for s, v in zip(now["sector_name"], now["relative_strength"])]
    bar = go.Figure(go.Bar(
        x=100 * now["relative_strength"], y=labels, orientation="h",
        marker=dict(color=[sector_color(s) for s in now["sector_name"]], cornerradius=4),
        hovertemplate="%{y}<extra></extra>"))
    bar.update_layout(height=320, yaxis=dict(autorange="reversed"), xaxis_ticksuffix=" pp", hovermode="closest",
                      xaxis_title="Relative 20-day momentum")
    st.plotly_chart(bar, use_container_width=True)

with right:
    st.subheader("Rank over time")
    weekly = ss.copy()
    weekly["week"] = weekly["price_date"].dt.to_period("W").dt.start_time
    weekly = weekly.sort_values("price_date").groupby(["week", "sector_name"]).last().reset_index()
    pivot = weekly.pivot(index="sector_name", columns="week", values="relative_strength").reindex(SECTOR_ORDER)
    ranks = weekly.pivot(index="sector_name", columns="week", values="strength_rank").reindex(SECTOR_ORDER)
    lim = float(pivot.abs().max().max() * 100) or 1.0
    heat = go.Figure(go.Heatmap(
        z=100 * pivot.values, x=pivot.columns, y=pivot.index, colorscale=DIVERGING, zmin=-lim, zmax=lim,
        text=ranks.values, texttemplate="%{text:.0f}", xgap=2, ygap=2,
        colorbar=dict(title="pp", thickness=10),
        hovertemplate="%{y}, week of %{x|%d %b}<br>rank %{text:.0f}, %{z:+.2f} pp<extra></extra>"))
    heat.update_layout(height=320, hovermode="closest")
    st.plotly_chart(heat, use_container_width=True)
    st.caption("Cell = week-end value; number = rank (1 = strongest). Blue above the universe average, red below.")

st.subheader("Relative strength")
line = go.Figure()
for sector in SECTOR_ORDER:
    s = ss[ss["sector_name"] == sector]
    line.add_trace(go.Scatter(x=s["price_date"], y=100 * s["relative_strength"], name=sector, mode="lines",
                              line=dict(width=2, color=sector_color(sector)), hovertemplate="%{y:+.2f} pp"))
line.add_hline(y=0, line=dict(color="#c3c2b7", width=1))
line.update_layout(height=360, yaxis_ticksuffix=" pp")
st.plotly_chart(line, use_container_width=True)

with st.expander("Data table"):
    st.dataframe(ss.sort_values(["price_date", "strength_rank"], ascending=[False, True]), hide_index=True,
                 use_container_width=True)

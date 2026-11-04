from datetime import datetime, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import STATUS, STATUS_ICON, header, load

header("Pipeline health", "Every scheduled job logs itself in pipeline_runs; freshness is checked against the NSE trading calendar.")

health = load("pipeline_health")
runs = load("pipeline_runs", 200)
fresh = load("freshness")
rejections = load("rejections")

n_fresh = int(fresh["is_fresh"].fillna(False).astype(bool).sum())
latest = pd.Timestamp(fresh["latest_price_date"].max()).date() if len(fresh) else None
expected = pd.Timestamp(fresh["expected_date"].max()).date() if len(fresh) else None
daily = runs[runs["job_name"] == "daily_update"]
last_daily = daily.iloc[0] if len(daily) else None
recent = daily.head(30)
ok_rate = 100 * (recent["status"] == "success").mean() if len(recent) else None

c = st.columns(4)
c[0].metric("Fresh stocks", f"{n_fresh} / {len(fresh)}", help="Stocks whose latest price is the expected trading day")
c[1].metric("Latest price date", latest.strftime("%d %b %Y") if latest else "–",
            None if latest is None or latest >= expected else f"expected {expected:%d %b}", delta_color="inverse",
            delta_arrow="off")
if last_daily is not None:
    age = datetime.now(timezone.utc) - last_daily["started_at"].to_pydatetime()
    c[2].metric("Last daily update", f"{STATUS_ICON.get(last_daily['status'], '')} {last_daily['status']}",
                f"{age.total_seconds() / 3600:.0f} h ago", delta_color="off", delta_arrow="off")
c[3].metric("Success rate (last 30 runs)", "–" if ok_rate is None else f"{ok_rate:.0f}%")

st.subheader("Last run of each job")
h = health.copy()
h["status"] = h["status"].map(lambda s: f"{STATUS_ICON.get(s, '')} {s}")
st.dataframe(h[["job_name", "status", "started_at", "finished_at", "rows_upserted", "message", "latest_price_date"]],
             hide_index=True, use_container_width=True,
             column_config={"job_name": "Job", "status": "Status", "rows_upserted": "Rows written",
                            "started_at": st.column_config.DatetimeColumn("Started", format="D MMM YYYY, HH:mm"),
                            "finished_at": st.column_config.DatetimeColumn("Finished", format="D MMM YYYY, HH:mm"),
                            "message": "Message", "latest_price_date": st.column_config.DateColumn("Latest price")})

st.subheader("Run history")
st.caption("Each mark is one run, placed by start time and duration. ✅ success · ⚠️ partial · ❌ failed")
r = runs.dropna(subset=["finished_at"]).copy()
r["duration_s"] = (r["finished_at"] - r["started_at"]).dt.total_seconds()
fig = go.Figure()
for status in ["success", "partial", "failed", "running"]:
    s = r[r["status"] == status]
    if s.empty:
        continue
    fig.add_trace(go.Scatter(
        x=s["started_at"], y=s["duration_s"], mode="markers", name=status,
        marker=dict(size=10, color=STATUS[status], line=dict(color="white", width=2),
                    symbol={"success": "circle", "partial": "triangle-up", "failed": "x", "running": "square"}[status]),
        customdata=s[["job_name", "message"]].values,
        hovertemplate="%{customdata[0]}<br>%{x|%d %b %H:%M} UTC<br>%{y:.0f} s<br>%{customdata[1]}<extra></extra>"))
fig.update_layout(height=320, yaxis_title="Duration (s)", hovermode="closest")
st.plotly_chart(fig, use_container_width=True)

left, right = st.columns(2)
with left:
    st.subheader("Data freshness")
    f = fresh.copy()
    f["state"] = f["is_fresh"].map(lambda v: "✅ fresh" if v else "❌ stale")
    st.dataframe(f[["ticker", "latest_price_date", "expected_date", "state"]], hide_index=True,
                 use_container_width=True, height=360,
                 column_config={"ticker": "Ticker", "latest_price_date": st.column_config.DateColumn("Latest"),
                                "expected_date": st.column_config.DateColumn("Expected"), "state": "State"})
with right:
    st.subheader("Rows rejected by validation")
    if rejections.empty:
        st.success("No rows were rejected.")
    else:
        st.dataframe(rejections, hide_index=True, use_container_width=True,
                     column_config={"rejected_on": st.column_config.DateColumn("Day"), "check_name": "Check",
                                    "n_rows": "Rows", "n_tickers": "Stocks"})
    st.caption("src/validation.py rejects impossible bars before insert and records them in rejected_prices.")

with st.expander("Recent runs"):
    st.dataframe(runs.head(50), hide_index=True, use_container_width=True)

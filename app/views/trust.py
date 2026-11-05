import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import SERIES, header, source
from trust_data import SYNTHETIC_NOTE, coverage_rate, coverage_totals, load_trust, reconciliation_by_category, revisions_changed

header("Data trust", "Session coverage, validation outcomes, revision capture and Yahoo vs NSE bhavcopy reconciliation, read from existing views.")


@st.cache_data(ttl=600, show_spinner=False)
def _load(mode: str):
    return load_trust(source())


try:
    data, is_synthetic = _load(source().mode)
except Exception as exc:
    st.error(f"Trust data is not available right now ({type(exc).__name__}).")
    st.stop()

if is_synthetic:
    st.warning(SYNTHETIC_NOTE, icon=":material/science:")

cov, quality, rev_sum, rev_rate, recon = (data[k] for k in
                                          ("coverage", "quality", "revision_summary", "revision_rate", "reconciliation"))
recon_cat = reconciliation_by_category(recon)
changed = revisions_changed(rev_sum)
rate = coverage_rate(cov)
match_share = recon_cat.loc[recon_cat["classification"] == "match", "share_pct"].sum() if len(recon_cat) else None

c = st.columns(4)
c[0].metric("Session coverage", "–" if rate is None else f"{rate:.2f}%",
            help="present / (present + missing + fetch_failed), all years")
c[1].metric("Rejected + suspect rows", f"{int(quality['n_rows'].sum()) if len(quality) else 0:,}")
c[2].metric("Values changed on refetch", f"{int(changed['n_differences'].sum()) if len(changed) else 0:,}")
c[3].metric("Yahoo = NSE close", "–" if match_share is None else f"{match_share:.1f}%",
            help="Share of reconciled stock-days classified as match")

st.subheader("Session coverage")
st.caption("From v_session_coverage_summary. One pair is one ticker on one expected session.")
tot = coverage_totals(cov)
left, right = st.columns([1, 2])
with left:
    st.dataframe(tot[["status", "pairs", "pct_of_year", "tickers"]], hide_index=True, use_container_width=True,
                 column_config={"status": "Status", "pairs": "Pairs", "pct_of_year": "% of all", "tickers": "Tickers"})
with right:
    by_year = cov[cov["year"].notna()].copy()
    by_year["year"] = by_year["year"].astype(int).astype(str)
    fig = go.Figure()
    for i, status in enumerate(by_year["status"].unique()):
        s = by_year[by_year["status"] == status]
        fig.add_trace(go.Bar(x=s["year"], y=s["pct_of_year"], name=status, marker_color=SERIES[i % len(SERIES)]))
    fig.update_layout(barmode="stack", height=300, yaxis_title="% of year", hovermode="closest")
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Rejected and suspect rows over time")
st.caption("Weekly counts from rejected_prices (validation) and quality_states (suspect, rejected).")
if quality.empty:
    st.success("No rejected or suspect rows recorded.")
else:
    q = quality.copy()
    q["week"] = pd.to_datetime(q["week"])
    fig = go.Figure()
    for i, state in enumerate(sorted(q["state"].unique())):
        s = q[q["state"] == state]
        fig.add_trace(go.Bar(x=s["week"], y=s["n_rows"], name=state, marker_color=SERIES[(i + 1) % len(SERIES)]))
    fig.update_layout(barmode="stack", height=300, yaxis_title="Rows per week")
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Revision capture")
st.caption("Stored values compared with a fresh fetch. From v_revision_capture_rate and v_revision_capture_summary.")
left, right = st.columns(2)
with left:
    st.dataframe(rev_rate, hide_index=True, use_container_width=True,
                 column_config={"sample": "Sample", "n_runs": "Runs", "rows_compared": "Rows compared",
                                "rows_differing": "Rows differing", "pct_rows_differing": "% differing"})
with right:
    st.dataframe(rev_sum, hide_index=True, use_container_width=True,
                 column_config={"sample": "Sample", "kind": "Kind", "field": "Field", "n_differences": "Changes",
                                "n_stock_days": "Stock-days", "n_tickers": "Tickers",
                                "mean_abs_diff_bp": "Mean |Δ| bp", "max_abs_diff_bp": "Max |Δ| bp"})

st.subheader("Yahoo vs NSE reconciliation by category")
st.caption("From v_reconciliation_summary (latest classification per ticker and day).")
if recon_cat.empty:
    st.info("No reconciliation results recorded.")
else:
    left, right = st.columns([3, 2])
    with left:
        fig = go.Figure(go.Bar(x=recon_cat["n_stock_days"], y=recon_cat["classification"], orientation="h",
                               marker_color=SERIES[0], customdata=recon_cat[["share_pct"]].values,
                               hovertemplate="%{y}: %{x:,} stock-days (%{customdata[0]:.2f}%)<extra></extra>"))
        fig.update_layout(height=320, xaxis_type="log", xaxis_title="Stock-days (log scale)", hovermode="closest",
                          yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)
    with right:
        st.dataframe(recon_cat, hide_index=True, use_container_width=True,
                     column_config={"classification": "Category", "n_stock_days": "Stock-days",
                                    "share_pct": st.column_config.NumberColumn("Share %", format="%.2f"),
                                    "mean_abs_diff_bp": st.column_config.NumberColumn("Mean |Δ| bp", format="%.1f"),
                                    "max_abs_diff_bp": st.column_config.NumberColumn("Max |Δ| bp", format="%.1f")})

if is_synthetic:
    st.caption(SYNTHETIC_NOTE)

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

from marketintel.datasource import DataSource, DataUnavailable, DbSource, DemoSource

SECTOR_ORDER = ["Auto", "Banking", "FMCG", "IT", "Pharma"]
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SECTOR_COLORS = dict(zip(SECTOR_ORDER, SERIES))
STATUS = {"success": "#0ca30c", "partial": "#fab219", "running": "#2a78d6", "failed": "#d03b3b"}
STATUS_ICON = {"success": "✅", "partial": "⚠️", "running": "⏳", "failed": "❌"}
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e7e6e2"
SURFACE = "#fcfcfb"
DIVERGING = [[0, "#d03b3b"], [0.5, "#f0efec"], [1, "#2a78d6"]]

pio.templates["mi"] = go.layout.Template(layout=go.Layout(
    font=dict(family="Inter, Segoe UI, system-ui, sans-serif", color=INK, size=13),
    paper_bgcolor=SURFACE, plot_bgcolor=SURFACE, colorway=SERIES,
    xaxis=dict(gridcolor=GRID, linecolor=GRID, zeroline=False, ticks="", showgrid=False),
    yaxis=dict(gridcolor=GRID, linecolor=GRID, zeroline=False, ticks=""),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, title_text=""),
    margin=dict(l=8, r=8, t=36, b=8), hovermode="x unified",
    hoverlabel=dict(bgcolor="white", bordercolor=GRID, font_color=INK),
))
pio.templates.default = "mi"

CSS = """
<style>
.block-container {padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1400px;}
h1 {font-weight: 700; letter-spacing: -0.02em;}
h2, h3 {letter-spacing: -0.01em;}
div[data-testid="stMetric"] {background: #fcfcfb; border: 1px solid #e7e6e2; border-radius: 12px; padding: 14px 16px;}
div[data-testid="stMetricValue"] {font-size: 1.65rem; font-weight: 600;}
div[data-testid="stMetricLabel"] p {color: #52514e; font-size: 0.82rem; text-transform: uppercase; letter-spacing: 0.04em;}
.mi-badge {display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.78rem; font-weight: 600;}
.mi-demo {background: #fff4d6; color: #6b4a00; border: 1px solid #f3d27a;}
.mi-live {background: #e3f4e3; color: #0a5c0a; border: 1px solid #9ad49a;}
.mi-sub {color: #52514e; margin-top: -0.6rem; margin-bottom: 1.2rem;}
</style>
"""


@st.cache_resource(show_spinner=False)
def _source(url: str) -> DataSource:
    return DbSource(url) if url else DemoSource()


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        try:
            url = st.secrets.get("DATABASE_URL", "")
        except Exception:
            url = ""
    return url


def source() -> DataSource:
    return _source(database_url())


@st.cache_data(ttl=600, show_spinner=False)
def cached(method: str, mode: str, *args, **kwargs):
    return getattr(source(), method)(*args, **kwargs)


def load(method: str, *args, **kwargs):
    try:
        return cached(method, source().mode, *args, **kwargs)
    except DataUnavailable as exc:
        st.error(f"Data is not available right now ({exc}). Try again in a minute.")
        st.stop()


def header(title: str, subtitle: str) -> None:
    st.markdown(CSS, unsafe_allow_html=True)
    src = source()
    badge = ('<span class="mi-badge mi-live">LIVE DATABASE</span>' if src.mode == "database"
             else '<span class="mi-badge mi-demo">DEMO MODE · SYNTHETIC DATA</span>')
    st.markdown(f"# {title} {badge}", unsafe_allow_html=True)
    st.markdown(f'<p class="mi-sub">{subtitle}</p>', unsafe_allow_html=True)


def sidebar_dates(default_days: int = 365, key: str = "range") -> tuple:
    lo, hi = load("date_range")
    options = {"1M": 31, "3M": 92, "6M": 183, "1Y": 365, "3Y": 1096, "Max": None}
    labels = list(options)
    default = next((k for k, v in options.items() if v == default_days), "1Y")
    choice = st.segmented_control("Period", labels, default=default, key=key) or default
    days = options[choice]
    start = lo if days is None else max(lo, hi - timedelta(days=days))
    return start, hi


def sidebar_footer() -> None:
    src = source()
    with st.sidebar:
        st.divider()
        if src.mode == "demo":
            st.caption("Demo mode: " + getattr(src, "about", "bundled synthetic sample data"))
            st.caption("Set `DATABASE_URL` (read-only role) to show the live database.")
        else:
            st.caption("Connected to the live database. All queries run in READ ONLY transactions on reporting views.")
        st.caption("[Source on GitHub](https://github.com/Bhavex/market-intelligence-pg)")


def pct(x, digits: int = 2) -> str:
    return "–" if x is None or pd.isna(x) else f"{x:+.{digits}f}%"


def money(x) -> str:
    return "–" if x is None or pd.isna(x) else f"₹{x:,.2f}"


def sector_color(name: str) -> str:
    return SECTOR_COLORS.get(name, SERIES[len(SECTOR_COLORS) % len(SERIES)])


def as_date(x) -> date:
    return pd.Timestamp(x).date()

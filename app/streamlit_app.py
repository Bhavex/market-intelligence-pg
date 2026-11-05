import common
import streamlit as st

st.set_page_config(page_title="NSE Sector Intelligence", page_icon="📈", layout="wide",
                   initial_sidebar_state="expanded")

pages = st.navigation({
    "Markets": [
        st.Page("views/overview.py", title="Sector overview", icon=":material/dashboard:", default=True),
        st.Page("views/company.py", title="Company detail", icon=":material/candlestick_chart:"),
        st.Page("views/strength.py", title="Sector strength", icon=":material/leaderboard:"),
        st.Page("views/anomalies.py", title="Anomalies", icon=":material/warning:"),
    ],
    "Operations": [
        st.Page("views/pipeline.py", title="Pipeline health", icon=":material/monitor_heart:"),
        st.Page("views/trust.py", title="Data trust", icon=":material/verified:"),
    ],
})
pages.run()
common.sidebar_footer()

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from streamlit.testing.v1 import AppTest

PAGES = ["overview", "company", "strength", "anomalies", "pipeline"]


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_in_demo_mode(page):
    at = AppTest.from_file(str(ROOT / "app" / "views" / f"{page}.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("DEMO MODE" in m.value for m in at.markdown)
    assert len(at.metric) >= 3


def test_entrypoint_runs_default_page():
    at = AppTest.from_file(str(ROOT / "app" / "streamlit_app.py"), default_timeout=60).run()
    assert not at.exception
    assert any(m.label == "Best sector (1M)" for m in at.metric)


def test_company_page_switches_company():
    at = AppTest.from_file(str(ROOT / "app" / "views" / "company.py"), default_timeout=60).run()
    at.sidebar.selectbox(key="company_sector").set_value("IT").run()
    at.sidebar.selectbox(key="company_ticker").set_value("INFY.NS").run()
    assert not at.exception
    assert at.metric[0].label == "Close"


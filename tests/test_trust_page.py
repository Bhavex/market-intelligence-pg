import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from streamlit.testing.v1 import AppTest

import trust_data


@pytest.fixture(autouse=True)
def demo_mode(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)


def test_page_renders_in_demo_mode_with_synthetic_label():
    at = AppTest.from_file(str(ROOT / "app" / "views" / "trust.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("DEMO MODE" in m.value for m in at.markdown)
    assert any("SYNTHETIC" in w.value for w in at.warning)
    assert [m.label for m in at.metric] == ["Session coverage", "Rejected + suspect rows",
                                            "Values changed on refetch", "Yahoo = NSE close"]
    assert {s.value for s in at.subheader} >= {"Session coverage", "Rejected and suspect rows over time",
                                               "Revision capture", "Yahoo vs NSE reconciliation by category"}


def test_page_is_registered_in_navigation():
    assert "views/trust.py" in (ROOT / "app" / "streamlit_app.py").read_text()


def test_all_queries_are_read_only_and_use_existing_objects():
    expected = {"coverage": "v_session_coverage_summary", "quality": "rejected_prices",
                "revision_summary": "v_revision_capture_summary", "revision_rate": "v_revision_capture_rate",
                "reconciliation": "v_reconciliation_summary"}
    for name, obj in expected.items():
        assert obj in trust_data.QUERIES[name]
        assert trust_data.is_read_only(trust_data.QUERIES[name])
    assert "quality_states" in trust_data.QUERIES["quality"]


@pytest.mark.parametrize("sql", ["DELETE FROM x", "SELECT 1; DROP TABLE x", "UPDATE x SET a=1", "insert into x"])
def test_is_read_only_rejects_writes(sql):
    assert not trust_data.is_read_only(sql)


def test_database_mode_runs_every_query_through_source():
    class FakeSource:
        mode = "database"

        def __init__(self):
            self.seen = []

        def query(self, sql):
            self.seen.append(sql)
            return pd.DataFrame()

    src = FakeSource()
    data, synthetic = trust_data.load_trust(src)
    assert synthetic is False
    assert set(data) == set(trust_data.QUERIES)
    assert src.seen == list(trust_data.QUERIES.values())


def test_demo_mode_returns_synthetic_data():
    data, synthetic = trust_data.load_trust(type("S", (), {"mode": "demo"})())
    assert synthetic is True
    assert set(data) == set(trust_data.QUERIES)
    assert all(not df.empty for df in data.values())


def test_synthetic_data_is_deterministic():
    a, b = trust_data.synthetic(), trust_data.synthetic()
    for k in a:
        pd.testing.assert_frame_equal(a[k], b[k])


def test_coverage_rate_uses_tradable_statuses_only():
    cov = pd.DataFrame({"year": [None, None, None, None], "status": ["present", "missing", "fetch_failed", "holiday"],
                        "pairs": [90, 5, 5, 1000]})
    assert trust_data.coverage_rate(cov) == pytest.approx(90.0)
    assert trust_data.coverage_rate(cov.iloc[0:0]) is None


def test_reconciliation_by_category_combines_formats_and_orders():
    recon = pd.DataFrame({
        "bhav_format": ["legacy_cm", "udiff_cm", "udiff_cm"],
        "classification": ["unexplained", "match", "unexplained"],
        "n_stock_days": [10, 80, 10],
        "mean_abs_diff_bp": [20.0, 1.0, 40.0],
        "max_abs_diff_bp": [50.0, 4.0, 90.0],
    })
    out = trust_data.reconciliation_by_category(recon)
    assert list(out["classification"]) == ["match", "unexplained"]
    row = out.set_index("classification").loc["unexplained"]
    assert row["n_stock_days"] == 20
    assert row["mean_abs_diff_bp"] == pytest.approx(30.0)
    assert row["max_abs_diff_bp"] == 90.0
    assert out["share_pct"].sum() == pytest.approx(100.0)


def test_reconciliation_by_category_empty():
    assert trust_data.reconciliation_by_category(pd.DataFrame()).empty


def test_revisions_changed_filters_kind():
    s = pd.DataFrame({"kind": ["value_changed", "missing_in_feed"], "n_differences": [3, 1]})
    assert list(trust_data.revisions_changed(s)["n_differences"]) == [3]

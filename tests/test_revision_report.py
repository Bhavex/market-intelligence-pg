from pathlib import Path

import pytest

import revision_report as R

FIX = Path(__file__).resolve().parent / "fixtures" / "revision_report"


def load(name):
    return R.load_json(FIX / name)


def test_empty_store_says_nothing_stored():
    text = R.render(*load("empty.json"))
    assert "No revision capture results are stored yet" in text


def test_rechecked_without_changes_says_no_revisions():
    text = R.render(*load("no_changes.json"))
    assert "Values rechecked (stock-days compared): 10" in text
    assert "No revisions found yet" in text


def test_counts_use_first_detection_and_ignore_non_value_kinds():
    s = R.summarise(*load("with_changes.json"))
    assert s["rechecked"] == 40
    assert s["changed_values"] == 3
    assert s["changed_stock_days"] == 2
    assert s["runs"] == 2
    assert s["by_ticker"] == {"INFY": (1, 30), "TCS": (1, 10)}
    assert s["by_field"] == {"close": 1, "volume": 1, "adj_close": 1}


def test_lag_and_age():
    s = R.summarise(*load("with_changes.json"))
    assert sorted(s["lags"]) == [2, 2, 585]
    assert s["by_age"] == {"0-7 days": 1, "over 1 year": 1}
    den = s["age_denominators"]
    assert sum(den.values()) == pytest.approx(40)
    assert den["over 1 year"] == pytest.approx(20)


def test_sizes_in_rupees_and_percent():
    s = R.summarise(*load("with_changes.json"))
    close = next(x for x in s["sizes"] if x["field"] == "close")
    assert close["diff"] == pytest.approx(2.0)
    assert close["pct"] == pytest.approx(0.05)
    adj = next(x for x in s["sizes"] if x["field"] == "adj_close")
    assert adj["diff"] == pytest.approx(-15.0)
    assert adj["pct"] == pytest.approx(-1.0)


def test_render_sections_and_units():
    text = R.render(*load("with_changes.json"))
    for heading in ("by ticker", "by field", "by age of data", "first detected change", "Size of changes"):
        assert heading in text
    assert "| TCS | 1 | 10 | 10.00% |" in text
    assert "+2.0000 Rs" in text
    assert "+100.0000 shares" in text
    assert "-1.0000%" in text


def test_rel_diff_fallback_when_missing():
    cov, cap = load("with_changes.json")
    cap = [dict(c, rel_diff=None) for c in cap]
    s = R.summarise(cov, cap)
    adj = next(x for x in s["sizes"] if x["field"] == "adj_close")
    assert adj["pct"] == pytest.approx(-1.0)


def test_cli_appends_to_output(tmp_path):
    out = tmp_path / "summary.md"
    out.write_text("existing\n")
    R.main(["--input", str(FIX / "with_changes.json"), "--output", str(out)])
    text = out.read_text()
    assert text.startswith("existing\n## Revision capture report")

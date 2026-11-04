import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import adjudication as A

TEST_DB = os.environ.get("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parent.parent


def candidates(n_tickers=6, days=80, flagged_every=7, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for t in range(n_tickers):
        dates = pd.bdate_range("2023-01-02", periods=days)
        px = 100 * np.cumprod(1 + rng.normal(0, 0.02, days))
        for i, d in enumerate(dates):
            reason = None
            if i % flagged_every == 3:
                reason = ["quality:jump_suspect", "validation:price_spike", "bhavcopy:unexplained"][i % 3]
            rows.append({"ticker": f"T{t}.NS", "price_date": d, "open": px[i], "high": px[i] * 1.01, "low": px[i] * 0.99,
                         "close": px[i], "volume": 1000, "stored": True,
                         "prev_date": dates[i - 1] if i else None, "prev_close": px[i - 1] if i else None,
                         "next_date": dates[i + 1] if i + 1 < days else None,
                         "next_close": px[i + 1] if i + 1 < days else None, "reasons": reason,
                         "ref_close": px[i] * 1.002 if i % 5 == 0 else None,
                         "ref_source": "nse_official" if i % 5 == 0 else None})
    return A.prepare(pd.DataFrame(rows))


class TestStrata:
    def test_size_buckets(self):
        assert [A.size_bucket(x) for x in (0, 49.9, 50, 199.9, 200, 999, 1000, -5000, float("nan"))] == [
            "<50bp", "<50bp", "50-200bp", "50-200bp", "200-1000bp", "200-1000bp", ">=1000bp", ">=1000bp", "unknown"]

    def test_reference_price_beats_previous_close_as_the_discrepancy(self):
        df = A.prepare(pd.DataFrame([{"ticker": "X", "price_date": "2024-01-02", "close": 101.0, "prev_close": 100.0,
                                      "next_close": 101.0, "ref_close": 100.5, "reasons": None}]))
        assert df.loc[0, "discrepancy_kind"] == "vs_reference"
        assert df.loc[0, "discrepancy_bp"] == pytest.approx(49.751243781)
        assert df.loc[0, "size_bucket"] == "<50bp" and df.loc[0, "stratum"] == "unflagged|none|<50bp"

    def test_primary_reason_prefers_validation_then_quality(self):
        assert A.primary_reason("bhavcopy:unexplained;quality:jump_suspect") == "quality:jump_suspect"
        assert A.primary_reason("quality:x;validation:price_spike") == "validation:price_spike"
        assert A.primary_reason(None) == "none"

    def test_allocation_by_hand(self):
        assert A.allocate({"a": 100, "b": 10, "c": 1}, 20, 3) == {"a": 16, "b": 3, "c": 1}
        assert A.allocate({"a": 5, "b": 5}, 50, 3) == {"a": 5, "b": 5}
        assert A.allocate({"a": 30, "b": 30, "c": 40}, 10, 1) == {"a": 3, "b": 3, "c": 4}


class TestSample:
    def test_every_stratum_is_sampled_and_tickers_spread(self):
        df = candidates()
        s = A.stratified_sample(df, 30, 30, seed=1)
        assert int(s["flagged"].sum()) == 30 and int((~s["flagged"]).sum()) == 30
        assert set(s.loc[s["flagged"], "stratum"]) == set(df.loc[df["flagged"], "stratum"])
        for _, g in s.groupby("stratum"):
            pool = df[df["stratum"] == g["stratum"].iloc[0]]
            assert g["ticker"].nunique() == min(len(g), pool["ticker"].nunique())
        assert (s["weight"] == s["stratum_population"] / s["stratum_sample"]).all()
        assert s["item_id"].is_unique

    def test_same_seed_same_sample(self):
        df = candidates()
        a, b = A.stratified_sample(df, 20, 20, seed=4), A.stratified_sample(df, 20, 20, seed=4)
        pd.testing.assert_frame_equal(a, b)
        assert not A.stratified_sample(df, 20, 20, seed=5)[["ticker", "price_date"]].equals(a[["ticker", "price_date"]])

    def test_labeller_sheets_are_blind_and_independently_ordered(self, tmp_path):
        s = A.stratified_sample(candidates(), 15, 15, seed=2)
        paths = A.export(s, tmp_path, seed=2)
        a, b, key = pd.read_csv(paths["A"]), pd.read_csv(paths["B"]), pd.read_csv(paths["key"])
        for sheet in (a, b):
            assert not {"flagged", "reason", "reasons", "stratum", "weight"} & set(sheet.columns)
            assert sheet["label"].isna().all() and set(sheet["item_id"]) == set(key["item_id"])
        assert a["item_id"].tolist() != b["item_id"].tolist()
        assert {"flagged", "reason", "weight"} <= set(key.columns)
        assert "real_error" in paths["instructions"].read_text()


ITEMS = [f"item-{i:04d}" for i in range(1, 11)]
LA = ["real_error"] * 3 + ["genuine_move"] * 3 + ["adjustment_difference"] * 2 + ["unknown", "real_error"]
LB = ["real_error", "real_error", "genuine_move", "genuine_move", "genuine_move", "real_error",
      "adjustment_difference", "genuine_move", "unknown", "real_error"]


def write(tmp_path, weights=None, a=LA, b=LB):
    key = pd.DataFrame({"item_id": ITEMS, "flagged": [True] * 6 + [False] * 4,
                        "reason": ["quality:jump_suspect"] * 3 + ["validation:price_spike"] * 3 + ["none"] * 4,
                        "weight": weights or [1.0] * 10})
    key.to_csv(tmp_path / "key.csv", index=False)
    pd.DataFrame({"item_id": ITEMS, "label": a}).to_csv(tmp_path / "a.csv", index=False)
    pd.DataFrame({"item_id": ITEMS[::-1], "label": b[::-1]}).to_csv(tmp_path / "b.csv", index=False)
    return tmp_path / "key.csv", tmp_path / "a.csv", tmp_path / "b.csv"


class TestScore:
    def test_agreement_and_kappa_by_hand(self, tmp_path):
        r = A.score(*write(tmp_path))
        assert r["labelled_by_both"] == 10 and r["agreement"]["percent"] == pytest.approx(0.7)
        assert r["agreement"]["cohen_kappa"] == pytest.approx(0.565217391)
        m = r["agreement"]["confusion"]["matrix"]
        assert m["real_error"]["genuine_move"] == 1 and m["genuine_move"]["real_error"] == 1
        assert m["adjustment_difference"]["genuine_move"] == 1 and m["real_error"]["real_error"] == 3
        assert [d["item_id"] for d in r["disagreements"]] == ["item-0003", "item-0006", "item-0008"]

    def test_precision_false_rejection_and_misses_by_hand(self, tmp_path):
        f = A.score(*write(tmp_path))["flags"]
        c = f["consensus"]
        assert (c["precision_strict"]["hits"], c["precision_strict"]["n"]) == (2, 4)
        assert c["false_rejection_rate"]["rate"] == pytest.approx(0.5)
        assert c["precision_strict"]["ci95"] == pytest.approx([0.150, 0.850], abs=1e-3)
        assert c["miss_rate_unflagged"]["rate"] == pytest.approx(0.5)
        assert c["miss_rate_unflagged"]["excluded_unknown"] == 1
        assert f["a"]["precision_strict"]["rate"] == pytest.approx(0.5) and f["b"]["false_rejection_rate"]["rate"] == pytest.approx(0.5)
        assert c["by_reason"]["quality:jump_suspect"]["precision_strict"]["rate"] == pytest.approx(1.0)
        assert c["by_reason"]["validation:price_spike"]["false_rejection_rate"]["rate"] == pytest.approx(1.0)

    def test_weights_reweight_strata(self, tmp_path):
        r = A.score(*write(tmp_path, weights=[3, 3, 1, 1, 1, 1, 1, 1, 1, 1]))
        assert r["flags"]["consensus"]["precision_strict"]["weighted_rate"] == pytest.approx(0.75)

    def test_a_resolution_file_settles_disagreements(self, tmp_path):
        key, a, b = write(tmp_path)
        pd.DataFrame({"item_id": ["item-0003", "item-0006", "item-0008"],
                      "label": ["real_error", "genuine_move", "adjustment_difference"]}).to_csv(tmp_path / "r.csv", index=False)
        r = A.score(key, a, b, tmp_path / "r.csv")
        assert r["consensus_items"] == 10
        assert r["flags"]["consensus"]["precision_strict"]["rate"] == pytest.approx(0.5)

    def test_bad_files_are_refused(self, tmp_path):
        key, a, b = write(tmp_path, a=["Real error"] + LA[1:])
        assert A.read_labels(a).iloc[0] == "real_error"
        pd.DataFrame({"item_id": ITEMS, "label": ["maybe"] + LA[1:]}).to_csv(a, index=False)
        with pytest.raises(A.LabelError, match="unknown label 'maybe'"):
            A.score(key, a, b)
        pd.DataFrame({"item_id": ITEMS[:1] * 2, "label": LA[:2]}).to_csv(a, index=False)
        with pytest.raises(A.LabelError, match="duplicate"):
            A.read_labels(a)

    def test_blank_labels_count_as_missing(self, tmp_path):
        key, a, b = write(tmp_path, a=[""] + LA[1:])
        r = A.score(key, a, b)
        assert r["missing"]["a"] == 1 and r["labelled_by_both"] == 9

    def test_score_cli_writes_json_and_markdown(self, tmp_path):
        key, a, b = write(tmp_path)
        out = subprocess.run([sys.executable, str(ROOT / "scripts" / "adjudication_score.py"), "--key", str(key),
                              "--a", str(a), "--b", str(b), "--out", str(tmp_path / "o")], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr
        assert "kappa 0.565" in out.stdout and (tmp_path / "o" / "adjudication_scores.json").exists()


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
def test_candidates_come_from_every_flag_source(tmp_path):
    import psycopg
    with psycopg.connect(TEST_DB) as conn:
        conn.execute("""INSERT INTO rejected_prices (ticker, price_date, check_name, detail, raw_row)
                        VALUES ('ITC.NS', '2026-06-10', 'price_spike', 'fixture',
                                '{"close": 999.0, "open": 990, "high": 1000, "low": 980, "volume": 5}'::jsonb)""")
        conn.execute("DELETE FROM daily_prices WHERE ticker = 'ITC.NS' AND price_date = '2026-06-10'")
        conn.execute("""INSERT INTO quality_states (ticker, price_date, state, reason, rule_version)
                        VALUES ('DABUR.NS', '2026-06-11', 'suspect', 'jump_suspect', 'fixture')""")
        df = A.load_candidates(conn)
        conn.rollback()
    itc = df[(df["ticker"] == "ITC.NS") & (df["price_date"] == "2026-06-10")].iloc[0]
    assert itc["flagged"] and itc["reason"] == "validation:price_spike" and itc["close"] == 999
    assert not itc["stored"] and itc["prev_close"] is not None
    dab = df[(df["ticker"] == "DABUR.NS") & (df["price_date"] == "2026-06-11")].iloc[0]
    assert dab["flagged"] and dab["reason"] == "quality:jump_suspect"
    assert (~df["flagged"]).sum() > 1000 and df[["ticker", "price_date"]].duplicated().sum() == 0

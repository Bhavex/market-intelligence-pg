import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import ml_evaluate as me
import ml_features as mf
from test_ml_features import _fake_prices


def test_top_mask_exact_count():
    conf = np.array([0.1, 0.4, 0.3, 0.2, 0.0])
    keep = me.top_mask(conf, 0.4)
    assert keep.sum() == 2 and keep[1] and keep[2]


def test_selective_table_shapes():
    rng = np.random.default_rng(0)
    y = rng.integers(0, 2, 1000)
    rets = rng.normal(0, 0.01, 1000)
    probs = {"knn": rng.uniform(0, 1, 1000), "xgboost": rng.uniform(0, 1, 1000)}
    sel = me.selective_table(y, rets, probs)
    assert set(sel.variant) == {"knn", "xgboost", "average", "agreement"}
    full = sel[(sel.variant == "knn") & (sel.coverage == 1.0)].iloc[0]
    assert full.n == 1000
    agree = sel[(sel.variant == "agreement") & (sel.coverage == 1.0)].iloc[0]
    assert agree.n < 1000


def test_walk_forward_runs(monkeypatch):
    df = mf.build_features(_fake_prices(n=1000, seed=7))
    monkeypatch.setattr(me, "XGB_PARAMS", {**me.XGB_PARAMS, "n_estimators": 10})
    wf = me.walk_forward(df, "sector5", {"k": 5, "n_rounds": 10}, start=pd.Timestamp("2023-01-01").date(), months=6, min_train=500)
    assert len(wf) >= 2
    assert set(wf.model) == {"knn", "xgboost"}
    assert (wf.n_train > 0).all()
    for _, r in wf.iterrows():
        assert r.fold_start < r.fold_end


def test_report_written(tmp_path, monkeypatch):
    df = mf.build_features(_fake_prices(n=1000, seed=7))
    monkeypatch.setattr(me, "XGB_PARAMS", {**me.XGB_PARAMS, "n_estimators": 10})
    monkeypatch.setattr(me, "KNN_GRID", [5, 15])
    monkeypatch.setattr(me, "WALK_START", pd.Timestamp("2024-01-01").date())
    orig = me.walk_forward
    monkeypatch.setattr(me, "walk_forward", lambda d, t, hp: orig(d, t, hp, min_train=500))
    sel, wf = me.run(df, "vol5", no_db=True, report_dir=tmp_path)
    text = (tmp_path / "ML_SELECTIVE_vol5.md").read_text()
    assert "Accuracy by coverage" in text and "Walk-forward" in text

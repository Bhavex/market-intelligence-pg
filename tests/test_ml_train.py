import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import ml_features as mf
import ml_train as mt
from test_ml_features import _fake_prices


def _run(target):
    df = mf.build_features(_fake_prices(n=1200, seed=3))
    params = {**mt.XGB_PARAMS, "n_estimators": 30}
    return mt.train_all(df, target, knn_grid=[5, 15], xgb_params=params)


def test_train_all_both_targets():
    for target in ("direction", "sector5"):
        models, results, base, base_val, meta = _run(target)
        assert set(models) == {"knn", "xgboost"}
        for r in results.values():
            assert 0 <= r["test"]["accuracy"] <= 1
            assert 0 < r["test"]["conf_coverage"] <= 0.3
        assert 1 <= results["xgboost"]["params"]["n_estimators"] <= 30
        text = mt.table(results, base, base_val, meta)
        assert "| knn |" in text and "baseline:" in text


def test_direction_baselines_consistent():
    df = mf.labeled(mf.build_features(_fake_prices(n=300)), "direction")
    b = mt.baselines(df, "direction")
    assert abs(b["same_as_today"]["accuracy"] + b["mean_reversion"]["accuracy"] - 1) < 1e-9

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from ml_features import DEFAULT_TARGET, FEATURES, TARGETS, horizon, labeled, load_or_build, time_split

MODEL_DIR = Path(__file__).resolve().parent.parent / "models"
KNN_GRID = [5, 11, 21, 31, 51, 75, 101, 151, 201]
XGB_PARAMS = dict(
    max_depth=3, learning_rate=0.03, n_estimators=800, subsample=0.8, colsample_bytree=0.8,
    min_child_weight=20, reg_lambda=1.0, objective="binary:logistic", eval_metric="logloss",
    n_jobs=4, random_state=42,
)
CONF_TOP = 0.2


def xy(df):
    return df[FEATURES].to_numpy(dtype=float), df["target"].to_numpy(dtype=int)


def metrics(y_true, y_pred):
    return dict(
        accuracy=round(float(accuracy_score(y_true, y_pred)), 4),
        balanced_acc=round(float(balanced_accuracy_score(y_true, y_pred)), 4),
        f1=round(float(f1_score(y_true, y_pred)), 4),
    )


def baselines(df, target):
    y = df["target"].to_numpy(dtype=int)
    if target == "direction":
        return {
            "always_up": metrics(y, np.ones_like(y)),
            "same_as_today": metrics(y, (df["ret_1"].to_numpy() > 0).astype(int)),
            "mean_reversion": metrics(y, (df["ret_1"].to_numpy() <= 0).astype(int)),
        }
    if target == "vol5":
        return {
            "always_high": metrics(y, np.ones_like(y)),
            "already_volatile": metrics(y, (df["vol_rank_20"].to_numpy() > 0.5).astype(int)),
        }
    return {
        "always_beats": metrics(y, np.ones_like(y)),
        "momentum_20d": metrics(y, (df["sector_rel_20"].to_numpy() > 0).astype(int)),
        "reversal_5d": metrics(y, (df["sector_rel_5"].to_numpy() <= 0).astype(int)),
    }


def make_knn(k):
    return Pipeline([("scale", StandardScaler()), ("knn", KNeighborsClassifier(n_neighbors=k, weights="distance", n_jobs=4))])


def tune_knn(train, val, grid=KNN_GRID):
    Xtr, ytr = xy(train)
    Xva, yva = xy(val)
    scores = {}
    for k in grid:
        m = make_knn(k).fit(Xtr, ytr)
        scores[k] = balanced_accuracy_score(yva, m.predict(Xva))
    best = max(scores, key=scores.get)
    return best, scores


def fit_xgb(train, val, params=XGB_PARAMS, early_stopping=50):
    Xtr, ytr = xy(train)
    Xva, yva = xy(val)
    m = XGBClassifier(**params, early_stopping_rounds=early_stopping)
    m.fit(Xtr, ytr, eval_set=[(Xva, yva)], verbose=False)
    return m, int(m.best_iteration) + 1


def evaluate(model, df, conf_top=CONF_TOP):
    X, y = xy(df)
    proba = model.predict_proba(X)[:, 1]
    pred = (proba >= 0.5).astype(int)
    out = metrics(y, pred)
    conf = np.abs(proba - 0.5)
    m = max(1, int(round(len(conf) * conf_top)))
    order = np.argsort(-conf, kind="stable")
    keep = np.zeros(len(conf), dtype=bool)
    keep[order[:m]] = True
    cut = float(conf[order[m - 1]])
    out["conf_accuracy"] = round(float(accuracy_score(y[keep], pred[keep])), 4) if keep.sum() else None
    out["conf_coverage"] = round(float(keep.mean()), 4)
    out["conf_threshold"] = round(float(cut), 4)
    return out


def train_all(df, target=DEFAULT_TARGET, knn_grid=KNN_GRID, xgb_params=XGB_PARAMS):
    lab = labeled(df, target)
    train, val, test = time_split(lab, embargo=horizon(target))
    trainval = pd.concat([train, val])
    best_k, knn_scores = tune_knn(train, val, knn_grid)
    knn = make_knn(best_k).fit(*xy(trainval))
    xgb_tuned, n_rounds = fit_xgb(train, val, xgb_params)
    final_params = {**xgb_params, "n_estimators": n_rounds}
    xgb = XGBClassifier(**final_params).fit(*xy(trainval))
    results = {
        "knn": {
            "params": {"k": best_k, "weights": "distance", "val_scores": {str(k): round(v, 4) for k, v in knn_scores.items()}},
            "val": evaluate(make_knn(best_k).fit(*xy(train)), val),
            "test": evaluate(knn, test),
        },
        "xgboost": {
            "params": {k: v for k, v in final_params.items() if k not in ("n_jobs", "objective", "eval_metric")},
            "val": evaluate(xgb_tuned, val),
            "test": evaluate(xgb, test),
        },
    }
    base = baselines(test, target)
    base_val = baselines(val, target)
    meta = dict(
        target=target,
        n_tickers=int(lab["ticker"].nunique()),
        train=(train["price_date"].min().date(), train["price_date"].max().date(), len(train)),
        val=(val["price_date"].min().date(), val["price_date"].max().date(), len(val)),
        test=(test["price_date"].min().date(), test["price_date"].max().date(), len(test)),
    )
    return {"knn": knn, "xgboost": xgb}, results, base, base_val, meta


def table(results, base, base_val, meta):
    lines = [
        f"target: {meta['target']} ({TARGETS[meta['target']]}), {meta['n_tickers']} stocks",
        f"train {meta['train'][0]} to {meta['train'][1]} ({meta['train'][2]} rows), "
        f"val {meta['val'][0]} to {meta['val'][1]} ({meta['val'][2]} rows), "
        f"test {meta['test'][0]} to {meta['test'][1]} ({meta['test'][2]} rows)",
        "",
        f"| model | val acc | test acc | test balanced acc | test F1 | high-conf acc (top {int(CONF_TOP*100)}%) |",
        "|---|---|---|---|---|---|",
    ]
    for name, r in results.items():
        t = r["test"]
        lines.append(f"| {name} | {r['val']['accuracy']:.4f} | {t['accuracy']:.4f} | {t['balanced_acc']:.4f} | {t['f1']:.4f} | {t['conf_accuracy']:.4f} |")
    for name, r in base.items():
        lines.append(f"| baseline: {name} | {base_val[name]['accuracy']:.4f} | {r['accuracy']:.4f} | {r['balanced_acc']:.4f} | {r['f1']:.4f} | - |")
    return "\n".join(lines)


def feature_importance(xgb):
    imp = xgb.feature_importances_
    order = np.argsort(imp)[::-1]
    return [(FEATURES[i], round(float(imp[i]), 4)) for i in order]


def save_models(models, target):
    MODEL_DIR.mkdir(exist_ok=True)
    for name, m in models.items():
        joblib.dump(m, MODEL_DIR / f"{name}_{target}.joblib")


def write_runs(conn, results, base, meta, run_kind="holdout"):
    ids = {}
    best_base = max(b["accuracy"] for b in base.values())
    for name, r in results.items():
        row = conn.execute(
            """INSERT INTO model_runs (model_name, run_kind, train_start, train_end, test_start, test_end,
                                       params, feature_names, n_train, n_test, accuracy, balanced_acc, f1, baseline_acc, notes)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING run_id""",
            (name, run_kind, meta["train"][0], meta["val"][1], meta["test"][0], meta["test"][1],
             json.dumps({**r["params"], "target": meta["target"], "n_tickers": meta["n_tickers"]}), FEATURES,
             meta["train"][2] + meta["val"][2], meta["test"][2],
             r["test"]["accuracy"], r["test"]["balanced_acc"], r["test"]["f1"], best_base,
             f"target={meta['target']}; high-confidence (top {int(CONF_TOP*100)}%) accuracy {r['test']['conf_accuracy']}"),
        ).fetchone()
        ids[name] = row[0]
    conn.commit()
    return ids


def write_report(path, text, importance, meta):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body = [
        f"# Prediction models: hold-out results ({meta['target']})",
        "",
        f"Generated by `python src/ml_train.py --target {meta['target']}` on {stamp}. Features are built from past data only "
        "(`src/ml_features.py --check` verifies this). Hyper-parameters were chosen on the validation year, "
        "then each model was refit on train + validation and scored once on the untouched test period. "
        f"The last {horizon(meta['target'])} sessions of train and validation are dropped (embargo) so no label overlaps the next period. "
        f"The high-confidence column keeps only the {int(CONF_TOP*100)}% of test rows where the model's probability is furthest from 0.5.",
        "",
        text,
        "",
        "## XGBoost feature importance (gain)",
        "",
        "| feature | importance |",
        "|---|---|",
    ] + [f"| {f} | {v:.4f} |" for f, v in importance]
    Path(path).write_text("\n".join(body) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=DEFAULT_TARGET, choices=list(TARGETS))
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--no-db", action="store_true")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()
    df = load_or_build(all_stocks=args.all, refresh=args.refresh)
    models, results, base, base_val, meta = train_all(df, args.target)
    text = table(results, base, base_val, meta)
    print(text)
    print()
    print("knn k by validation balanced accuracy:", results["knn"]["params"]["val_scores"], "-> chosen k =", results["knn"]["params"]["k"])
    print("xgboost rounds after early stopping:", results["xgboost"]["params"]["n_estimators"])
    imp = feature_importance(models["xgboost"])
    print("xgboost top features:", imp[:5])
    save_models(models, args.target)
    print(f"models saved to {MODEL_DIR}")
    report = args.report or f"docs/ML_RESULTS_{args.target}.md"
    write_report(report, text, imp, meta)
    print(f"report written to {report}")
    if not args.no_db:
        from db import get_conn
        with get_conn() as conn:
            ids = write_runs(conn, results, base, meta)
        print("model_runs rows:", ids)


if __name__ == "__main__":
    main()

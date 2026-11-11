import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score
from xgboost import XGBClassifier

from ml_features import DEFAULT_TARGET, FEATURES, TARGETS, horizon, labeled, load_or_build, time_split
from ml_train import KNN_GRID, XGB_PARAMS, baselines, fit_xgb, make_knn, tune_knn, xy

COVERAGES = [1.0, 0.5, 0.2, 0.1, 0.05, 0.02]
COST_BPS = 10
WALK_START = date(2020, 1, 1)
WALK_MONTHS = 6


def top_mask(conf, coverage):
    m = max(1, int(round(len(conf) * coverage)))
    order = np.argsort(-conf, kind="stable")
    keep = np.zeros(len(conf), dtype=bool)
    keep[order[:m]] = True
    return keep


def selective_table(y, rets, probs):
    rows = []
    p_knn, p_xgb = probs["knn"], probs["xgboost"]
    p_avg = (p_knn + p_xgb) / 2
    agree = (p_knn >= 0.5) == (p_xgb >= 0.5)
    variants = {
        "knn": (p_knn, np.abs(p_knn - 0.5), np.ones_like(agree)),
        "xgboost": (p_xgb, np.abs(p_xgb - 0.5), np.ones_like(agree)),
        "average": (p_avg, np.abs(p_avg - 0.5), np.ones_like(agree)),
        "agreement": (p_avg, np.minimum(np.abs(p_knn - 0.5), np.abs(p_xgb - 0.5)), agree),
    }
    for name, (p, conf, allowed) in variants.items():
        pred = (p >= 0.5).astype(int)
        for cov in COVERAGES:
            keep = top_mask(np.where(allowed, conf, -1.0), cov) & allowed
            n = int(keep.sum())
            if n == 0:
                continue
            acc = float(accuracy_score(y[keep], pred[keep]))
            sign = np.where(pred[keep] == 1, 1.0, -1.0)
            net = float(np.mean(sign * rets[keep]) * 1e4 - COST_BPS)
            se = float(np.sqrt(acc * (1 - acc) / n))
            rows.append(dict(variant=name, coverage=cov, n=n, accuracy=round(acc, 4), se=round(se, 4),
                             z_vs_coinflip=round((acc - 0.5) / se, 2) if se else None, net_bps_per_call=round(net, 2)))
    return pd.DataFrame(rows)


def per_year(y, rets, p, dates, coverage=0.2):
    conf = np.abs(p - 0.5)
    pred = (p >= 0.5).astype(int)
    out = []
    years = pd.DatetimeIndex(dates).year
    for yr in sorted(set(years)):
        m = years == yr
        keep = top_mask(np.where(m, conf, -1.0), coverage * m.mean()) & m
        out.append(dict(year=int(yr), n_all=int(m.sum()), acc_all=round(float(accuracy_score(y[m], pred[m])), 4),
                        n_conf=int(keep.sum()), acc_conf=round(float(accuracy_score(y[keep], pred[keep])), 4)))
    return pd.DataFrame(out)


def holdout_probs(df, target):
    lab = labeled(df, target)
    train, val, test = time_split(lab, embargo=horizon(target))
    trainval = pd.concat([train, val])
    best_k, _ = tune_knn(train, val, KNN_GRID)
    _, n_rounds = fit_xgb(train, val, XGB_PARAMS)
    knn = make_knn(best_k).fit(*xy(trainval))
    xgb = XGBClassifier(**{**XGB_PARAMS, "n_estimators": n_rounds}).fit(*xy(trainval))
    Xte, yte = xy(test)
    probs = {"knn": knn.predict_proba(Xte)[:, 1], "xgboost": xgb.predict_proba(Xte)[:, 1]}
    hp = {"k": best_k, "n_rounds": n_rounds}
    return test, yte, probs, hp


def walk_forward(df, target, hp, start=WALK_START, months=WALK_MONTHS, min_train=5000):
    lab = labeled(df, target).sort_values("price_date")
    emb = horizon(target)
    folds = []
    cut = pd.Timestamp(start)
    last = lab["price_date"].max()
    while cut < last:
        end = cut + pd.DateOffset(months=months)
        train = lab[lab["price_date"] <= cut - pd.tseries.offsets.BDay(emb)]
        test = lab[(lab["price_date"] > cut) & (lab["price_date"] <= end)]
        if len(test) == 0 or len(train) < min_train:
            cut = end
            continue
        Xtr, ytr = xy(train)
        Xte, yte = xy(test)
        knn = make_knn(hp["k"]).fit(Xtr, ytr)
        xgb = XGBClassifier(**{**XGB_PARAMS, "n_estimators": hp["n_rounds"]}).fit(Xtr, ytr)
        for name, m in (("knn", knn), ("xgboost", xgb)):
            p = m.predict_proba(Xte)[:, 1]
            pred = (p >= 0.5).astype(int)
            keep = top_mask(np.abs(p - 0.5), 0.2)
            folds.append(dict(model=name, fold_start=cut.date(), fold_end=min(end, last).date(), n_train=len(train), n_test=len(test),
                              accuracy=round(float(accuracy_score(yte, pred)), 4),
                              conf_accuracy=round(float(accuracy_score(yte[keep], pred[keep])), 4),
                              baseline_acc=round(max(b["accuracy"] for b in baselines(test, target).values()), 4)))
        cut = end
    return pd.DataFrame(folds)


def md_table(df):
    cols = list(df.columns)
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        lines.append("| " + " | ".join(str(r[c]) for c in cols) + " |")
    return "\n".join(lines)


def write_report(path, target, n_tickers, test, sel, yearly, wf, hp):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    wf_summary = wf.groupby("model").agg(folds=("accuracy", "size"), mean_acc=("accuracy", "mean"), mean_conf_acc=("conf_accuracy", "mean"),
                                         mean_baseline=("baseline_acc", "mean"), folds_above_baseline=("accuracy", lambda s: int((s.values > wf.loc[s.index, "baseline_acc"].values).sum()))).round(4).reset_index()
    body = [
        f"# Selective prediction: {target}",
        "",
        f"Target: {TARGETS[target]}. {n_tickers} stocks. Generated by `python src/ml_evaluate.py --all --target {target}` on {stamp}.",
        f"Hold-out test period {test['price_date'].min().date()} to {test['price_date'].max().date()} ({len(test)} rows). "
        f"KNN k={hp['k']}, XGBoost rounds={hp['n_rounds']} (chosen on the 2023 validation year).",
        "",
        "## Accuracy by coverage (how often the system speaks)",
        "",
        f"`coverage` is the share of test rows on which a call is issued, keeping the most confident ones. `agreement` issues a call only when both models point the same way. "
        f"`net_bps_per_call` is the mean target return in the predicted direction, in basis points, minus {COST_BPS} bps cost. `z_vs_coinflip` is the number of standard errors above 50%.",
        "",
        md_table(sel),
        "",
        "## Stability by year (XGBoost, 20% coverage)",
        "",
        md_table(yearly),
        "",
        f"## Walk-forward (retrain every {WALK_MONTHS} months from {WALK_START}, fixed hyper-parameters)",
        "",
        md_table(wf_summary),
        "",
        md_table(wf),
    ]
    Path(path).write_text("\n".join(body) + "\n")


def write_db(conn, target, n_tickers, test, sel, wf, hp):
    s20 = sel[(sel.variant == "xgboost") & (sel.coverage == 0.2)].iloc[0]
    for _, r in wf.iterrows():
        conn.execute(
            """INSERT INTO model_runs (model_name, run_kind, train_start, train_end, test_start, test_end, params, feature_names,
                                       n_train, n_test, accuracy, baseline_acc, notes)
               VALUES (%s,'walk_forward',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (r["model"], date(2015, 1, 1), r["fold_start"], r["fold_start"], r["fold_end"],
             json.dumps({**hp, "target": target, "n_tickers": n_tickers}), FEATURES, int(r["n_train"]), int(r["n_test"]),
             float(r["accuracy"]), float(r["baseline_acc"]), f"target={target}; high-confidence accuracy {r['conf_accuracy']}"),
        )
    conn.execute(
        """INSERT INTO model_runs (model_name, run_kind, train_start, train_end, test_start, test_end, params, feature_names, n_test,
                                   accuracy, strategy_return, notes)
           VALUES ('selective_xgboost','holdout',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (date(2015, 1, 1), test["price_date"].min().date(), test["price_date"].min().date(), test["price_date"].max().date(),
         json.dumps({**hp, "target": target, "coverage": 0.2, "n_tickers": n_tickers}), FEATURES, int(s20["n"]),
         float(s20["accuracy"]), float(s20["net_bps_per_call"]) / 1e4, f"target={target}; coverage table in docs/ML_SELECTIVE_{target}.md"),
    )
    conn.commit()


def run(df, target, no_db=False, report_dir="docs"):
    test, y, probs, hp = holdout_probs(df, target)
    rets = test["target_ret"].to_numpy(dtype=float)
    sel = selective_table(y, rets, probs)
    yearly = per_year(y, rets, probs["xgboost"], test["price_date"].to_numpy())
    wf = walk_forward(df, target, hp)
    n_tickers = int(test["ticker"].nunique())
    path = Path(report_dir) / f"ML_SELECTIVE_{target}.md"
    write_report(path, target, n_tickers, test, sel, yearly, wf, hp)
    print(f"\n=== {target}: {TARGETS[target]} ===")
    print(md_table(sel[sel.coverage.isin([1.0, 0.2, 0.05])]))
    print()
    print(md_table(wf.groupby("model")[["accuracy", "conf_accuracy", "baseline_acc"]].mean().round(4).reset_index()))
    print(f"report: {path}")
    if not no_db:
        from db import get_conn
        with get_conn() as conn:
            write_db(conn, target, n_tickers, test, sel, wf, hp)
        print("model_runs updated")
    return sel, wf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="all", choices=list(TARGETS) + ["all"])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--no-db", action="store_true")
    args = ap.parse_args()
    df = load_or_build(all_stocks=args.all)
    targets = list(TARGETS) if args.target == "all" else [args.target]
    for t in targets:
        run(df, t, args.no_db)


if __name__ == "__main__":
    main()

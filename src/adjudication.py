import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

LABELS = ("real_error", "genuine_move", "adjustment_difference", "unknown")
ALIASES = {
    "real error": "real_error", "error": "real_error", "real_error": "real_error",
    "genuine market move": "genuine_move", "genuine move": "genuine_move", "market move": "genuine_move",
    "genuine_move": "genuine_move", "adjustment difference": "adjustment_difference",
    "adjustment": "adjustment_difference", "adjustment_difference": "adjustment_difference",
    "unknown": "unknown", "?": "unknown",
}
SIZE_BUCKETS = ((50, "<50bp"), (200, "50-200bp"), (1000, "200-1000bp"), (float("inf"), ">=1000bp"))
REASON_PRIORITY = ("validation", "quality", "bhavcopy")
EVIDENCE = ["item_id", "ticker", "price_date", "weekday", "open", "high", "low", "close", "volume", "prev_date",
            "prev_close", "next_date", "next_close", "return_bp", "next_return_bp", "ref_source", "ref_close",
            "ref_diff_bp", "label", "notes"]

CANDIDATES_SQL = """
WITH flags AS (
    SELECT ticker, price_date, 'quality:' || reason AS reason FROM v_quality_current WHERE state IN ('suspect', 'rejected')
    UNION ALL
    SELECT ticker, price_date, 'validation:' || check_name FROM rejected_prices
    UNION ALL
    SELECT ticker, trade_date, 'bhavcopy:' || classification FROM v_reconciliation_latest
    WHERE classification NOT IN ('match', 'missing_in_yahoo', 'missing_in_bhavcopy') AND ticker IS NOT NULL
), flag_rows AS (
    SELECT ticker, price_date, string_agg(DISTINCT reason, ';' ORDER BY reason) AS reasons FROM flags GROUP BY 1, 2
), raw AS (
    SELECT DISTINCT ON (ticker, price_date) ticker, price_date,
           (raw_row->>'open')::numeric AS open, (raw_row->>'high')::numeric AS high, (raw_row->>'low')::numeric AS low,
           (raw_row->>'close')::numeric AS close, (raw_row->>'volume')::numeric AS volume
    FROM rejected_prices ORDER BY ticker, price_date, rejected_at DESC
), rows AS (
    SELECT p.ticker, p.price_date, p.open, p.high, p.low, p.close, p.volume::numeric AS volume, TRUE AS stored
    FROM daily_prices p
    UNION ALL
    SELECT r.ticker, r.price_date, r.open, r.high, r.low, r.close, r.volume, FALSE
    FROM raw r WHERE NOT EXISTS (SELECT 1 FROM daily_prices p WHERE p.ticker = r.ticker AND p.price_date = r.price_date)
), ordered AS (
    SELECT x.*,
           LAG(price_date) OVER w AS prev_date, LAG(close) OVER w AS prev_close,
           LEAD(price_date) OVER w AS next_date, LEAD(close) OVER w AS next_close
    FROM (SELECT * FROM rows WHERE stored) x
    WINDOW w AS (PARTITION BY ticker ORDER BY price_date)
), with_unstored AS (
    SELECT * FROM ordered
    UNION ALL
    SELECT r.*, pv.price_date, pv.close, nx.price_date, nx.close
    FROM rows r
    LEFT JOIN LATERAL (SELECT price_date, close FROM daily_prices p WHERE p.ticker = r.ticker AND p.price_date < r.price_date
                       ORDER BY price_date DESC LIMIT 1) pv ON TRUE
    LEFT JOIN LATERAL (SELECT price_date, close FROM daily_prices p WHERE p.ticker = r.ticker AND p.price_date > r.price_date
                       ORDER BY price_date LIMIT 1) nx ON TRUE
    WHERE NOT r.stored
)
SELECT w.ticker, w.price_date, w.open, w.high, w.low, w.close, w.volume, w.stored, w.prev_date, w.prev_close,
       w.next_date, w.next_close, f.reasons,
       COALESCE(n.close, a.close, b.bhav_close) AS ref_close,
       CASE WHEN n.close IS NOT NULL THEN 'nse_official' WHEN a.close IS NOT NULL THEN 'bse_' || a.feed
            WHEN b.bhav_close IS NOT NULL THEN 'nse_bhavcopy' END AS ref_source
FROM with_unstored w
LEFT JOIN flag_rows f ON f.ticker = w.ticker AND f.price_date = w.price_date
LEFT JOIN LATERAL (SELECT close FROM reference_prices n WHERE n.ticker = w.ticker AND n.price_date = w.price_date
                   ORDER BY fetched_at DESC LIMIT 1) n ON TRUE
LEFT JOIN LATERAL (SELECT feed, close FROM alt_feed_prices a WHERE a.ticker = w.ticker AND a.price_date = w.price_date
                   LIMIT 1) a ON TRUE
LEFT JOIN LATERAL (SELECT bhav_close FROM v_reconciliation_latest b WHERE b.ticker = w.ticker AND b.trade_date = w.price_date
                   LIMIT 1) b ON TRUE
"""


def _bp(a, b):
    a, b = pd.to_numeric(a, errors="coerce"), pd.to_numeric(b, errors="coerce")
    return (a / b - 1) * 1e4


def size_bucket(bp) -> str:
    if bp is None or not np.isfinite(bp):
        return "unknown"
    for limit, name in SIZE_BUCKETS:
        if abs(bp) < limit:
            return name
    return SIZE_BUCKETS[-1][1]


def primary_reason(reasons) -> str:
    if not isinstance(reasons, str) or not reasons:
        return "none"
    parts = reasons.split(";")
    return sorted(parts, key=lambda r: (REASON_PRIORITY.index(r.split(":")[0]) if r.split(":")[0] in REASON_PRIORITY
                                        else len(REASON_PRIORITY), r))[0]


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["price_date"] = pd.to_datetime(out["price_date"])
    out["return_bp"] = _bp(out["close"], out["prev_close"])
    out["next_return_bp"] = _bp(out["next_close"], out["close"])
    out["ref_diff_bp"] = _bp(out["close"], out.get("ref_close"))
    has_ref = out["ref_diff_bp"].notna()
    out["discrepancy_kind"] = np.where(has_ref, "vs_reference", "vs_previous_close")
    out["discrepancy_bp"] = np.where(has_ref, out["ref_diff_bp"].abs(), out["return_bp"].abs())
    out["flagged"] = out["reasons"].notna() & (out["reasons"].astype(str) != "")
    out["reason"] = out["reasons"].map(primary_reason)
    out["size_bucket"] = out["discrepancy_bp"].map(size_bucket)
    out["year"] = out["price_date"].dt.year
    out["stratum"] = np.where(out["flagged"], "flagged", "unflagged") + "|" + out["reason"] + "|" + out["size_bucket"]
    return out


def load_candidates(conn) -> pd.DataFrame:
    cur = conn.execute(CANDIDATES_SQL)
    cols = [c.name for c in cur.description]
    return prepare(pd.DataFrame(cur.fetchall(), columns=cols))


def allocate(sizes: dict, n: int, minimum: int) -> dict:
    total = sum(sizes.values())
    if total == 0 or n <= 0:
        return {k: 0 for k in sizes}
    if n >= total:
        return dict(sizes)
    alloc = {k: min(v, max(minimum, int(round(n * v / total)))) for k, v in sizes.items()}
    while sum(alloc.values()) > n:
        k = max(alloc, key=lambda s: (alloc[s] - minimum, alloc[s], s))
        if alloc[k] <= 1:
            break
        alloc[k] -= 1
    while sum(alloc.values()) < n:
        room = [k for k in alloc if alloc[k] < sizes[k]]
        if not room:
            break
        k = max(room, key=lambda s: (sizes[s] - alloc[s], s))
        alloc[k] += 1
    return alloc


def spread_pick(group: pd.DataFrame, k: int, rng: np.random.Generator) -> pd.DataFrame:
    if k >= len(group):
        return group
    queues = []
    tickers = list(group["ticker"].unique())
    rng.shuffle(tickers)
    for t in tickers:
        g = group[group["ticker"] == t]
        years = list(g["year"].unique())
        rng.shuffle(years)
        by_year = [list(rng.permutation(g.index[g["year"] == y])) for y in years]
        order = []
        while any(by_year):
            for q in by_year:
                if q:
                    order.append(q.pop())
        queues.append(order)
    picked = []
    while len(picked) < k:
        for q in queues:
            if q and len(picked) < k:
                picked.append(q.pop(0))
    return group.loc[picked]


def stratified_sample(df: pd.DataFrame, n_flagged: int = 200, n_unflagged: int = 200, seed: int = 0,
                      minimum: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    parts = []
    for flagged, n in ((True, n_flagged), (False, n_unflagged)):
        pool = df[df["flagged"] == flagged]
        sizes = pool.groupby("stratum").size().to_dict()
        alloc = allocate(sizes, n, minimum)
        for stratum in sorted(alloc):
            if alloc[stratum]:
                g = pool[pool["stratum"] == stratum]
                pick = spread_pick(g, alloc[stratum], rng).copy()
                pick["stratum_population"] = len(g)
                pick["stratum_sample"] = len(pick)
                parts.append(pick)
    sample = pd.concat(parts) if parts else df.head(0).assign(stratum_population=[], stratum_sample=[])
    sample = sample.sort_values(["flagged", "stratum", "ticker", "price_date"], ascending=[False, True, True, True])
    sample["weight"] = sample["stratum_population"] / sample["stratum_sample"]
    sample.insert(0, "item_id", [f"item-{i:04d}" for i in range(1, len(sample) + 1)])
    return sample.reset_index(drop=True)


INSTRUCTIONS = """# Labelling instructions

Label every row in your own file without talking to the other labeller. Do not open `adjudication_key.csv`: it
says which rows the pipeline flagged, and seeing that would bias the label.

Put exactly one of these in the `label` column:

| label | use it when |
|---|---|
| `real_error` | the stored price is wrong: a bad print, a stale value, a value from the wrong day, a typo-sized jump that reverses |
| `genuine_move` | the price is right and the market really moved (results, news, index event, circuit limit) |
| `adjustment_difference` | both prices are right but on different bases: split, bonus or dividend adjustment, or a close vs last-traded difference |
| `unknown` | the evidence in the row and what you can check is not enough to decide |

Evidence in each row: open, high, low, close and volume as stored; the previous and next stored closes and the moves
to them in basis points; and, where one exists, an independent close (`ref_source`: NSE official, BSE feed or the NSE
bhavcopy) with the difference in basis points. You may check exchange announcements, corporate-action calendars and
news. Write what you checked in `notes`.
"""


def export(sample: pd.DataFrame, out: Path, labellers=("A", "B"), seed: int = 0) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    s = sample.copy()
    s["weekday"] = s["price_date"].dt.day_name()
    for col in ("prev_date", "next_date"):
        if col in s:
            s[col] = pd.to_datetime(s[col]).dt.date
    s["price_date"] = s["price_date"].dt.date
    s["label"], s["notes"] = "", ""
    key_cols = ["item_id", "ticker", "price_date", "flagged", "reason", "reasons", "size_bucket", "discrepancy_kind",
                "discrepancy_bp", "stratum", "stratum_population", "stratum_sample", "weight"]
    s[[c for c in key_cols if c in s]].to_csv(out / "adjudication_key.csv", index=False)
    paths = {"key": out / "adjudication_key.csv"}
    for i, who in enumerate(labellers):
        order = np.random.default_rng(seed + 1 + i).permutation(len(s))
        sheet = s.iloc[order][[c for c in EVIDENCE if c in s]]
        num = sheet.select_dtypes("number").columns
        sheet[num] = sheet[num].round(4)
        path = out / f"labels_{who}.csv"
        sheet.to_csv(path, index=False)
        paths[who] = path
    (out / "INSTRUCTIONS.md").write_text(INSTRUCTIONS)
    paths["instructions"] = out / "INSTRUCTIONS.md"
    return paths


class LabelError(ValueError):
    pass


def read_labels(path: Path) -> pd.Series:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "item_id" not in df or "label" not in df:
        raise LabelError(f"{path}: needs item_id and label columns")
    if df["item_id"].duplicated().any():
        raise LabelError(f"{path}: duplicate item_id {df.loc[df['item_id'].duplicated(), 'item_id'].iloc[0]}")
    raw = df["label"].str.strip().str.lower()
    norm = raw.map(lambda x: ALIASES.get(x, ALIASES.get(x.replace("-", " ").replace("_", " "), None)) if x else None)
    bad = df.loc[raw.ne("") & norm.isna(), ["item_id", "label"]]
    if len(bad):
        raise LabelError(f"{path}: unknown label {bad.iloc[0]['label']!r} for {bad.iloc[0]['item_id']}; use one of {LABELS}")
    return pd.Series(norm.to_numpy(), index=df["item_id"].to_numpy(), name=Path(path).stem)


def cohen_kappa(a: pd.Series, b: pd.Series, categories=LABELS) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = float((a.to_numpy() == b.to_numpy()).mean())
    pa = a.value_counts(normalize=True).reindex(categories, fill_value=0.0)
    pb = b.value_counts(normalize=True).reindex(categories, fill_value=0.0)
    pe = float((pa * pb).sum())
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)


def wilson(k: float, n: float, z: float = 1.959963984540054) -> tuple:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (centre - half, centre + half)


def _rate(labels: pd.Series, weights: pd.Series, hits: set, exclude=("unknown",)) -> dict:
    keep = labels.notna() & ~labels.isin(exclude)
    lab, w = labels[keep], weights[keep]
    k, n = int(lab.isin(hits).sum()), int(len(lab))
    wk, wn = float(w[lab.isin(hits)].sum()), float(w.sum())
    lo, hi = wilson(k, n)
    return {"hits": k, "n": n, "rate": k / n if n else None, "ci95": [lo, hi] if n else None,
            "weighted_rate": wk / wn if wn else None, "excluded_unknown": int(labels.isin(exclude).sum())}


def flag_quality(key: pd.DataFrame, labels: pd.Series) -> dict:
    lab = labels.reindex(key["item_id"]).set_axis(key.index)
    flagged = key["flagged"].astype(str).str.lower().isin(["true", "1"])
    w = pd.to_numeric(key["weight"], errors="coerce").fillna(1.0)
    out = {
        "precision_strict": _rate(lab[flagged], w[flagged], {"real_error"}),
        "precision_lenient": _rate(lab[flagged], w[flagged], {"real_error", "adjustment_difference"}),
        "false_rejection_rate": _rate(lab[flagged], w[flagged], {"genuine_move"}),
        "miss_rate_unflagged": _rate(lab[~flagged], w[~flagged], {"real_error"}),
        "by_reason": {},
    }
    for reason, idx in key[flagged].groupby("reason").groups.items():
        out["by_reason"][reason] = {"precision_strict": _rate(lab[idx], w[idx], {"real_error"}),
                                    "false_rejection_rate": _rate(lab[idx], w[idx], {"genuine_move"})}
    return out


def score(key_path: Path, a_path: Path, b_path: Path, resolved_path: Path | None = None) -> dict:
    key = pd.read_csv(key_path, dtype={"item_id": str})
    a, b = read_labels(a_path), read_labels(b_path)
    ids = list(key["item_id"])
    unknown_ids = sorted((set(a.index) | set(b.index)) - set(ids))
    if unknown_ids:
        raise LabelError(f"labels for items not in the key: {unknown_ids[:3]}")
    a, b = a.reindex(ids), b.reindex(ids)
    both = a.notna() & b.notna()
    aa, bb = a[both], b[both]
    confusion = pd.crosstab(pd.Categorical(aa, LABELS), pd.Categorical(bb, LABELS), dropna=False)
    consensus = a.where(both & (a == b))
    if resolved_path is not None:
        consensus = consensus.fillna(read_labels(resolved_path).reindex(ids))
    disagreements = [{"item_id": i, a.name: a[i], b.name: b[i]} for i in ids if both[i] and a[i] != b[i]]
    result = {
        "items": len(ids), "labelled_by_both": int(both.sum()),
        "missing": {a.name: int(a.isna().sum()), b.name: int(b.isna().sum())},
        "agreement": {"percent": float((aa == bb).mean()) if len(aa) else None, "cohen_kappa": cohen_kappa(aa, bb),
                      "confusion": {"rows": a.name, "columns": b.name,
                                    "matrix": {r: {c: int(confusion.loc[r, c]) for c in LABELS} for r in LABELS}}},
        "label_counts": {a.name: a.value_counts().to_dict(), b.name: b.value_counts().to_dict()},
        "consensus_items": int(consensus.notna().sum()), "disagreements": disagreements,
        "flags": {"consensus": flag_quality(key, consensus), a.name: flag_quality(key, a), b.name: flag_quality(key, b)},
    }
    return result


def _pct(r: dict) -> str:
    if not r or r.get("rate") is None:
        return "n/a"
    lo, hi = r["ci95"]
    return f"{100 * r['rate']:.1f}% ({100 * lo:.1f}-{100 * hi:.1f}), n={r['n']}"


def markdown(result: dict) -> str:
    ag = result["agreement"]
    lines = ["# Adjudication results", "",
             f"Items: {result['items']}; labelled by both: {result['labelled_by_both']}; consensus: {result['consensus_items']}.",
             f"Agreement: {100 * ag['percent']:.1f}%; Cohen's kappa {ag['cohen_kappa']:.3f}." if ag["percent"] is not None
             else "Agreement: n/a.", "",
             "| measure | consensus | " + " | ".join(k for k in result["flags"] if k != "consensus") + " |",
             "|---|---|" + "---|" * (len(result["flags"]) - 1)]
    for m, name in (("precision_strict", "precision (real error)"),
                    ("precision_lenient", "precision (real error or adjustment)"),
                    ("false_rejection_rate", "false-rejection rate (genuine move)"),
                    ("miss_rate_unflagged", "real errors among unflagged rows")):
        lines.append(f"| {name} | " + " | ".join(_pct(v[m]) for v in result["flags"].values()) + " |")
    lines += ["", "Rates exclude `unknown` labels; intervals are Wilson 95%. Weighted rates (in the JSON) reweight each "
              "stratum to its population.", ""]
    return "\n".join(lines)

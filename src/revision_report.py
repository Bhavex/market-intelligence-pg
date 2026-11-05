import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime

AGE_BUCKETS = ((0, 7, "0-7 days"), (8, 30, "8-30 days"), (31, 90, "31-90 days"), (91, 365, "91-365 days"),
               (366, 10 ** 6, "over 1 year"))
LAG_BUCKETS = ((0, 1, "0-1 days"), (2, 3, "2-3 days"), (4, 7, "4-7 days"), (8, 30, "8-30 days"),
               (31, 10 ** 6, "over 30 days"))
MAX_CHANGE_ROWS = 50

COVERAGE_SQL = """
SELECT run_id, ticker, sample, window_start, window_end, rows_compared, rows_differing, checked_at
FROM revision_capture_coverage
"""

CAPTURES_SQL = """
SELECT run_id, ticker, price_date, sample, kind, field, stored_value::float8, fetched_value::float8,
       rel_diff::float8, stored_loaded_at, detected_at
FROM revision_captures
"""


def _day(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()


def _bucket(days, buckets):
    for lo, hi, label in buckets:
        if lo <= days <= hi:
            return label
    return buckets[0][2] if days < 0 else buckets[-1][2]


def _pct(n, d):
    return f"{n / d * 100:.2f}%" if d else "n/a"


def _quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = (len(xs) - 1) * q
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def _table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def first_detections(captures):
    first = {}
    for c in captures:
        if c.get("kind") != "value_changed":
            continue
        key = (c["ticker"], _day(c["price_date"]), c.get("field"))
        if key not in first or _day(c["detected_at"]) < _day(first[key]["detected_at"]):
            first[key] = c
    return list(first.values())


def age_denominators(coverage):
    out = defaultdict(float)
    for r in coverage:
        n = r.get("rows_compared") or 0
        checked = _day(r.get("checked_at"))
        start, end = _day(r.get("window_start")), _day(r.get("window_end"))
        if not n or checked is None or start is None or end is None:
            continue
        lo, hi = (checked - end).days, (checked - start).days
        span = hi - lo + 1
        for blo, bhi, label in AGE_BUCKETS:
            overlap = min(hi, bhi) - max(lo, blo) + 1
            if overlap > 0:
                out[label] += n * overlap / span
    return out


def summarise(coverage, captures):
    changes = first_detections(captures)
    rechecked = sum(r.get("rows_compared") or 0 for r in coverage)
    stock_days = {(c["ticker"], _day(c["price_date"])) for c in changes}
    by_ticker_checked = defaultdict(int)
    for r in coverage:
        by_ticker_checked[r["ticker"]] += r.get("rows_compared") or 0
    by_ticker_changed = defaultdict(set)
    by_field = defaultdict(int)
    by_age = defaultdict(set)
    lags = []
    sizes = []
    for c in changes:
        d = _day(c["price_date"])
        detected = _day(c["detected_at"])
        lag = (detected - d).days
        by_ticker_changed[c["ticker"]].add(d)
        by_field[c.get("field") or "unknown"] += 1
        by_age[_bucket(lag, AGE_BUCKETS)].add((c["ticker"], d))
        lags.append(lag)
        stored, fetched = c.get("stored_value"), c.get("fetched_value")
        diff = fetched - stored if stored is not None and fetched is not None else None
        rel = c.get("rel_diff")
        if rel is None and diff is not None and stored:
            rel = diff / abs(stored)
        sizes.append({"ticker": c["ticker"], "price_date": d, "field": c.get("field"), "stored": stored,
                      "fetched": fetched, "diff": diff, "pct": rel * 100 if rel is not None else None,
                      "lag_days": lag})
    return {"rechecked": rechecked, "changed_stock_days": len(stock_days), "changed_values": len(changes),
            "by_ticker": {t: (len(by_ticker_changed.get(t, ())), by_ticker_checked.get(t, 0))
                          for t in sorted(set(by_ticker_checked) | set(by_ticker_changed))},
            "by_field": dict(by_field), "by_age": {k: len(v) for k, v in by_age.items()},
            "age_denominators": dict(age_denominators(coverage)), "lags": lags, "sizes": sizes,
            "runs": len({r.get("run_id") for r in coverage})}


def _num(v, fmt="{:,.4f}"):
    return "n/a" if v is None else fmt.format(v)


def render(coverage, captures):
    s = summarise(coverage, captures)
    out = ["## Revision capture report", ""]
    if not coverage and not captures:
        out += ["No revision capture results are stored yet. Nothing has been rechecked, so there is nothing "
                "to report."]
        return "\n".join(out) + "\n"
    out += [f"- Capture runs: {s['runs']}",
            f"- Values rechecked (stock-days compared): {s['rechecked']:,}",
            f"- Stock-days with a changed value: {s['changed_stock_days']:,} "
            f"({_pct(s['changed_stock_days'], s['rechecked'])})",
            f"- Individual field values changed: {s['changed_values']:,}", ""]
    if not s["sizes"]:
        out += ["No revisions found yet: every rechecked value matched the stored value."]
        return "\n".join(out) + "\n"
    out += ["### Revision rate by ticker", ""]
    out += _table(["Ticker", "Changed stock-days", "Rechecked", "Rate"],
                  [(t, ch, ck, _pct(ch, ck)) for t, (ch, ck) in s["by_ticker"].items()])
    out += ["", "### Revision rate by field", ""]
    out += _table(["Field", "Changed values", "Rechecked", "Rate"],
                  [(f, n, s["rechecked"], _pct(n, s["rechecked"])) for f, n in sorted(s["by_field"].items())])
    out += ["", "### Revision rate by age of data", "",
            "Age is days from the trading date to the recheck. Rechecked counts per age band are estimated from "
            "each run's window.", ""]
    out += _table(["Age", "Changed stock-days", "Rechecked (est.)", "Rate"],
                  [(label, s["by_age"].get(label, 0), round(s["age_denominators"].get(label, 0)),
                    _pct(s["by_age"].get(label, 0), s["age_denominators"].get(label, 0)))
                   for _, _, label in AGE_BUCKETS])
    lags = s["lags"]
    out += ["", "### Time from trading date to first detected change", "",
            f"Min {min(lags)} days, median {_quantile(lags, 0.5):g} days, 90th percentile "
            f"{_quantile(lags, 0.9):g} days, max {max(lags)} days.", ""]
    counts = defaultdict(int)
    for x in lags:
        counts[_bucket(x, LAG_BUCKETS)] += 1
    out += _table(["Delay", "Changes"], [(label, counts.get(label, 0)) for _, _, label in LAG_BUCKETS])
    price = [x for x in s["sizes"] if x["field"] != "volume"]
    pcts = [abs(x["pct"]) for x in price if x["pct"] is not None]
    rupees = [abs(x["diff"]) for x in price if x["diff"] is not None]
    out += ["", "### Size of changes", ""]
    if price:
        out += [f"Price fields: median absolute change Rs {_quantile(rupees, 0.5):,.4f} "
                f"({_quantile(pcts, 0.5):.4f}%), max Rs {max(rupees):,.4f} ({max(pcts):.4f}%).", ""]
    ranked = sorted(s["sizes"], key=lambda x: -abs(x["pct"] or 0))
    rows = []
    for x in ranked[:MAX_CHANGE_ROWS]:
        unit = "shares" if x["field"] == "volume" else "Rs"
        rows.append((x["ticker"], x["price_date"].isoformat(), x["field"], _num(x["stored"]), _num(x["fetched"]),
                     f"{_num(x['diff'], '{:+,.4f}')} {unit}", _num(x["pct"], "{:+.4f}%"), x["lag_days"]))
    out += _table(["Ticker", "Trading date", "Field", "Stored", "Fetched", "Change", "Change %", "Days later"], rows)
    if len(ranked) > MAX_CHANGE_ROWS:
        out += ["", f"Showing the {MAX_CHANGE_ROWS} largest of {len(ranked)} changes by percent."]
    return "\n".join(out) + "\n"


def _rows(cur):
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_db():
    from db import get_conn
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(COVERAGE_SQL)
        coverage = _rows(cur)
        cur.execute(CAPTURES_SQL)
        captures = _rows(cur)
    return coverage, captures


def load_json(path):
    with open(path) as f:
        data = json.load(f)
    return data.get("coverage", []), data.get("captures", [])


def main(argv=None):
    p = argparse.ArgumentParser(description="Summarise stored revision capture results as Markdown.")
    p.add_argument("--input", help="JSON file with 'coverage' and 'captures' lists instead of the database")
    p.add_argument("--output", help="Append the report to this file instead of printing it")
    a = p.parse_args(argv)
    coverage, captures = load_json(a.input) if a.input else load_db()
    text = render(coverage, captures)
    if a.output:
        with open(a.output, "a") as f:
            f.write(text)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()

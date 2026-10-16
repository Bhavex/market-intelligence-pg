"""Collects the data-trust results (sql/08-10) into one dict, used by the site export
(site/data/trust.json) and printed as a Markdown report (GitHub step summary, paper tables).

Usage:  python src/trust_report.py            # Markdown to stdout
        python src/trust_report.py --json     # the raw dict
"""
import argparse
import datetime as dt
import json
import math
from decimal import Decimal

from db import get_conn


def _clean(v):
    if isinstance(v, Decimal):
        v = float(v)
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, dt.timedelta):
        return round(v.total_seconds() / 3600, 2)          # hours
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


def rows(conn, sql, params=None):
    cur = conn.execute(sql, params)
    names = [d.name for d in cur.description]
    return [{k: _clean(v) for k, v in zip(names, r)} for r in cur.fetchall()]


def collect(conn) -> dict:
    d = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="minutes")}
    d["versions"] = rows(conn, """
        SELECT COUNT(*) AS n_versions,
               COUNT(*) FILTER (WHERE sys_to IS NOT NULL) AS n_superseded,
               COUNT(DISTINCT (ticker, price_date)) AS n_stock_days,
               MIN(sys_from) AS history_since
        FROM price_versions""")[0]
    d["revisions"] = rows(conn, "SELECT * FROM v_revision_summary ORDER BY n_revisions DESC")
    d["settlement"] = rows(conn, """
        SELECT COUNT(*) AS n_revised_stock_days,
               COUNT(*) FILTER (WHERE first_seen - session_final_at(price_date) < INTERVAL '3 days') AS n_live,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY settled_after_session)
                   FILTER (WHERE first_seen - session_final_at(price_date) < INTERVAL '3 days') AS median_settle,
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY ABS(first_to_final_bp))
                   FILTER (WHERE first_seen - session_final_at(price_date) < INTERVAL '3 days') AS median_first_to_final_bp
        FROM v_settlement""")[0]
    d["example"] = rows(conn, """
        SELECT r.ticker, c.company_name, r.price_date, r.old_known_from, r.revised_at, r.old_close, r.new_close,
               r.close_change_bp, r.revision_kind
        FROM v_price_revisions r JOIN companies c ON c.ticker = r.ticker
        WHERE r.revision_kind IN ('intraday_as_close', 'price_restatement', 'hourly_fill_replaced')
        ORDER BY (r.revision_kind = 'intraday_as_close') DESC, ABS(r.close_change_bp) DESC NULLS LAST
        LIMIT 1""")
    d["agreement"] = rows(conn, "SELECT * FROM v_source_agreement_summary ORDER BY source DESC")
    d["coverage"] = rows(conn, "SELECT * FROM v_reference_coverage ORDER BY source, status")
    d["disagreements"] = rows(conn, "SELECT * FROM v_source_disagreements LIMIT 10")
    d["trust_summary"] = rows(conn, "SELECT * FROM v_trust_summary ORDER BY period, trust_grade")
    d["latest_trust"] = rows(conn, """SELECT ticker, company_name, price_date, trust_score, trust_grade, trust_flags
                                      FROM v_latest_trust ORDER BY trust_score, ticker""")
    run = rows(conn, "SELECT * FROM fragility_runs ORDER BY frag_run_id DESC LIMIT 1")
    d["fragility_run"] = run[0] if run else None
    d["fragility"] = rows(conn, "SELECT scenario, signal, n_compared, n_flipped, flip_pct, ci_low_pct, ci_high_pct "
                                "FROM v_fragility_latest")
    d["backtest"] = rows(conn, """SELECT scenario, metric, value FROM backtest_sensitivity
                                  WHERE frag_run_id = (SELECT MAX(frag_run_id) FROM fragility_runs)
                                  ORDER BY scenario, metric""")
    return d


SIGNAL_NAMES = {"trend": "Trend (UP/DOWN/NEUTRAL)", "is_anomaly": "Anomaly flag", "breakout_52w": "52-week breakout",
                "strong_breakout": "Strong breakout", "rsi_zone": "RSI zone (<30 / 30-70 / >70)",
                "momentum_sign": "20-day momentum sign", "sector_leader": "Strongest sector of the day"}


def markdown(d: dict) -> str:
    out = ["## Data trust study", ""]
    v = d["versions"]
    out.append(f"**Point-in-time history:** {v['n_versions']:,} versions of {v['n_stock_days']:,} stock-days; "
               f"{v['n_superseded']:,} superseded (revised). History since {str(v['history_since'])[:10]}.")
    out += ["", "### Revisions by kind", "", "| Kind | Revisions | Stock-days | Median abs. close change (bp) | Max (bp) |",
            "|---|---:|---:|---:|---:|"]
    out += [f"| {r['revision_kind']} | {r['n_revisions']} | {r['n_stock_days']} | {r['median_abs_close_change_bp']} | "
            f"{r['max_abs_close_change_bp']} |" for r in d["revisions"]] or ["| none yet | | | | |"]
    s = d["settlement"]
    out += ["", f"**Settlement:** {s['n_live']} live-captured stock-days were revised after first print; median time "
                f"to final value {s['median_settle']} h after the session; median first-print error "
                f"{s['median_first_to_final_bp']} bp."]
    out += ["", "### Yahoo vs official exchange close", "",
            "Yahoo's .NS prices are NSE prices: the NSE row is Yahoo's accuracy, the BSE row is the gap "
            "between two different exchanges' closing prices (not a Yahoo error).", "",
            "| Source | Compared | Sessions | Agree % | Mean abs. diff (bp) | Max (bp) | First-print agree % |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    out += [f"| {r['source']} | {r['n_compared']} | {r['n_sessions']} | {r['agree_pct']} | {r['mean_abs_diff_bp']} | "
            f"{r['max_abs_diff_bp']} | {r['first_print_agree_pct']} |" for r in d["agreement"]] or ["| no official data yet | | | | | | |"]
    out += ["", "Download log: " + (", ".join(f"{r['source']} {r['status']} {r['n_files']}" for r in d["coverage"]) or "none")]
    if d["fragility_run"]:
        val = d["fragility_run"]["validation"] or {}
        out += ["", "### Signal fragility (flip rate, % of comparisons, 95% Wilson interval)", "",
                f"Python replica vs database signals: {json.dumps(val)}", "",
                "| Scenario | Signal | Compared | Flipped | Flip % | 95% CI |", "|---|---|---:|---:|---:|---|"]
        out += [f"| {r['scenario']} | {SIGNAL_NAMES.get(r['signal'], r['signal'])} | {r['n_compared']:,} | {r['n_flipped']:,} | "
                f"{r['flip_pct']} | {r['ci_low_pct']}-{r['ci_high_pct']} |" for r in d["fragility"]]
        out += ["", "### Backtest sensitivity", "", "| Scenario | Metric | Value |", "|---|---|---:|"]
        out += [f"| {r['scenario']} | {r['metric']} | {r['value']} |" for r in d["backtest"]]
    grades = [r for r in d["trust_summary"] if r["period"] == "last_30_sessions"]
    out += ["", "**Trust grades, last 30 sessions:** " + (", ".join(f"{r['trust_grade']} {r['pct']}%" for r in grades) or "n/a")]
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    with get_conn() as conn:
        d = collect(conn)
    print(json.dumps(d, indent=1, default=str) if args.json else markdown(d))


if __name__ == "__main__":
    main()

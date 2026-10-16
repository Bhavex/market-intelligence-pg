"""Verify the 52-week signals. Read-only. Exit code 1 if anything fails.

1. runs every check in sql/validation/52w_checks.sql (0 bad rows = pass);
2. recomputes the four columns independently in pandas/Decimal for a few tickers straight from
   daily_prices and compares with the database (must match exactly after rounding);
3. checks that each ticker has exactly 252 leading NULL rows (explaining any difference);
4. prints breakout days per sector per year and the 5 most recent breakouts.

Usage:  python src/verify_52w.py [--tickers ICICIBANK.NS MARUTI.NS TCS.NS]"""
import argparse
import re
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pandas as pd

from db import get_conn

CHECK_FILE = Path(__file__).resolve().parent.parent / "sql" / "validation" / "52w_checks.sql"
DEFAULT_TICKERS = ["ICICIBANK.NS", "MARUTI.NS", "TCS.NS"]      # Banking, Auto, IT
CENT = Decimal("0.01")


def sql_checks():
    for chunk in re.split(r"^-- CHECK ", CHECK_FILE.read_text(), flags=re.M)[1:]:
        head, *body = chunk.splitlines()
        name, meaning = head.split(":", 1)
        sql = "\n".join(l for l in body if not l.lstrip().startswith("--")).strip().rstrip(";")
        yield name.strip(), meaning.strip(), sql


def run_sql_checks(conn) -> bool:
    print("== SQL checks (bad rows; 0 = PASS) ==")
    ok = True
    for name, meaning, sql in sql_checks():
        bad = conn.execute(sql).fetchone()[0]
        ok &= bad == 0
        print(f"{'PASS' if bad == 0 else 'FAIL'}  {name:26} {bad:>7}  {meaning}")
    return ok


def q2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding=ROUND_HALF_UP)            # PostgreSQL ROUND(numeric) rounds half away from zero


def recompute(conn, ticker):
    """Independent recomputation: pandas for the 252-row window, Decimal for the arithmetic."""
    px = pd.DataFrame(conn.execute(
        "SELECT price_date, adj_close FROM daily_prices WHERE ticker = %s ORDER BY price_date", (ticker,)).fetchall(),
        columns=["price_date", "adj_close"])
    fl = px["adj_close"].astype(float)
    # window = previous 252 rows (shift(1) drops today); min_periods=252 counts non-NaN values = COUNT(adj_close) = 252
    hi = fl.shift(1).rolling(252, min_periods=252).max()
    out = []
    for d, adj, h in zip(px["price_date"], px["adj_close"], hi):
        if pd.isna(h) or adj is None:
            out.append((d, None, None, None))
            continue
        h = Decimal(str(round(h, 4)))                          # a max of 4-dp values, so exact
        brk = adj > h
        out.append((d, q2(h), brk, q2((adj / h - Decimal(1)) * 100)))
    rec = pd.DataFrame(out, columns=["price_date", "high_52w", "breakout_52w", "pct_from_52w_high"])
    db = pd.DataFrame(conn.execute(
        "SELECT signal_date, high_52w, breakout_52w, pct_from_52w_high, strong_breakout, volume_zscore_20d "
        "FROM signals WHERE ticker = %s ORDER BY signal_date", (ticker,)).fetchall(),
        columns=["price_date", "db_high", "db_breakout", "db_pct", "db_strong", "z"])
    m = rec.merge(db, on="price_date", how="outer", indicator=True)
    # strong_breakout recomputed from the recomputed breakout + the stored z-score (NULL -> FALSE)
    m["strong_breakout"] = [None if b is None or pd.isna(b) else bool(b) and (z is not None and not pd.isna(z) and z > 2)
                            for b, z in zip(m["breakout_52w"], m["z"])]
    return m


def eq(a, b):
    na, nb = a is None or (not isinstance(a, (bool, Decimal)) and pd.isna(a)), b is None or (not isinstance(b, (bool, Decimal)) and pd.isna(b))
    return (na and nb) or (not na and not nb and a == b)


def run_pandas_check(conn, tickers) -> bool:
    print("\n== Independent pandas/Decimal recomputation ==")
    ok = True
    for t in tickers:
        m = recompute(conn, t)
        bad = {c: 0 for c in ("high_52w", "breakout_52w", "pct_from_52w_high", "strong_breakout")}
        examples = []
        for r in m.itertuples(index=False):
            for col, dbcol in (("high_52w", "db_high"), ("breakout_52w", "db_breakout"),
                               ("pct_from_52w_high", "db_pct"), ("strong_breakout", "db_strong")):
                if not eq(getattr(r, col), getattr(r, dbcol)):
                    bad[col] += 1
                    if len(examples) < 3:
                        examples.append((r.price_date, col, getattr(r, col), getattr(r, dbcol)))
        n_val = int(m["db_high"].notna().sum())
        good = sum(bad.values()) == 0 and (m["_merge"] == "both").all()
        ok &= good
        print(f"{'PASS' if good else 'FAIL'}  {t:14} rows={len(m)} with-values={n_val} "
              f"breakouts={int(m['db_breakout'].fillna(False).astype(bool).sum())} mismatches={bad}")
        for e in examples:
            print("      e.g.", e)
    return ok


def run_null_prefix(conn) -> bool:
    print("\n== Leading NULL rows per ticker (expected exactly 252) ==")
    rows = conn.execute("""
        SELECT p.ticker, COUNT(*) FILTER (WHERE p.adj_close IS NULL) AS n_adj_null,
               MIN(p.price_date) FILTER (WHERE s.high_52w IS NOT NULL) AS first_value_date,
               COUNT(*) FILTER (WHERE s.high_52w IS NULL)              AS n_null
        FROM daily_prices p JOIN signals s ON s.ticker = p.ticker AND s.signal_date = p.price_date
        GROUP BY p.ticker ORDER BY p.ticker""").fetchall()
    ok, exact = True, 0
    for ticker, n_adj_null, first_date, n_null in rows:
        if first_date is None:
            # fewer than 253 sessions so far (e.g. a recently listed research stock): every row must be NULL
            total = conn.execute("SELECT COUNT(*) FROM signals WHERE ticker = %s", (ticker,)).fetchone()[0]
            if n_null == total and total <= 252 + n_adj_null * 253:
                print(f"ok    {ticker}: only {total} sessions so far, all NULL as expected")
            else:
                ok = False
                print(f"FAIL  {ticker}: no 52-week value yet but {total} sessions")
            continue
        lead = conn.execute("SELECT COUNT(*) FROM signals WHERE ticker = %s AND signal_date < %s", (ticker, first_date)).fetchone()[0]
        if lead == 252 and n_null == 252:
            exact += 1
        # a missing adj_close removes that row and blanks the next 252 rows (window needs 252 non-null priors)
        elif n_adj_null > 0 and lead == 252 and n_null <= 252 + n_adj_null * 253:
            print(f"ok    {ticker}: 252 leading NULLs + {n_null - 252} more, explained by {n_adj_null} row(s) with NULL adj_close")
        else:
            ok = False
            print(f"FAIL  {ticker}: {lead} leading NULL rows, {n_null} NULL rows in total, {n_adj_null} rows with NULL adj_close")
    print(f"{'PASS' if ok else 'FAIL'}  {exact}/{len(rows)} tickers have exactly 252 NULL rows (all leading); the rest are explained above")
    return ok


def show_tables(conn):
    pd.set_option("display.width", 200)
    df = pd.DataFrame(conn.execute("""
        SELECT EXTRACT(YEAR FROM price_date)::int AS year, sector_name, SUM(breakout_count)::int AS breakout_days
        FROM v_sector_daily GROUP BY 1, 2 ORDER BY 1, 2""").fetchall(), columns=["year", "sector", "n"])
    print("\n== Breakout stock-days per sector per year (stock-days where adj_close > prior 252-day high) ==")
    print(df.pivot(index="year", columns="sector", values="n").fillna(0).astype(int).assign(total=lambda x: x.sum(axis=1)).to_string())
    print("\n== 5 most recent breakouts ==")
    print(pd.DataFrame(conn.execute("""
        SELECT s.signal_date, s.ticker, p.adj_close, s.high_52w, s.pct_from_52w_high AS pct, s.strong_breakout AS strong, s.volume_zscore_20d AS vol_z
        FROM signals s JOIN daily_prices p ON p.ticker = s.ticker AND p.price_date = s.signal_date
        WHERE s.breakout_52w ORDER BY s.signal_date DESC, s.pct_from_52w_high DESC LIMIT 5""").fetchall(),
        columns=["date", "ticker", "adj_close", "high_52w", "pct", "strong", "vol_z"]).to_string(index=False))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", nargs="*", default=DEFAULT_TICKERS)
    args = ap.parse_args()
    with get_conn() as conn:
        ok = run_sql_checks(conn)
        ok &= run_pandas_check(conn, args.tickers)
        ok &= run_null_prefix(conn)
        show_tables(conn)
    print("\nRESULT:", "ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

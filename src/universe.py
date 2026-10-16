"""Research universe: sync the stock list for the data-trust study from NSE's own index files.

The dashboard keeps its 25 stocks (companies.in_dashboard = TRUE). For the study we add
  * every member of the NIFTY 50                       -> cap_group 'large'
  * 25 members of the NIFTY SMALLCAP 100, chosen by a  -> cap_group 'small'
    fixed rule: sorted by symbol, every 4th one (positions 1, 5, 9, ...). The sample is
    FROZEN once it has 25 stocks: later index rebalances never add more small caps, so the
    small-cap group stays the same stocks and results stay comparable over time.
New stocks get in_dashboard = FALSE, so the website, Power BI views and the strict daily checks
never see them. Stocks are never removed when they leave an index (their history stays useful);
index_membership records which lists a stock was taken from.

NSE publishes each index's constituents as CSV (columns: Company Name, Industry, Symbol, Series,
ISIN Code). Several mirrors are tried; if all fail the universe is left unchanged.

Usage:  python src/universe.py
        python src/universe.py --file "NIFTY 50" ind_nifty50list.csv --file "NIFTY SMALLCAP 100" ind_niftysmallcap100list.csv
"""
import argparse
import io
import urllib.request

import pandas as pd

from db import get_conn, pipeline_run
from official_prices import HEADERS

INDEXES = {
    "NIFTY 50": "ind_nifty50list.csv",
    "NIFTY SMALLCAP 100": "ind_niftysmallcap100list.csv",
}
MIRRORS = [
    "https://nsearchives.nseindia.com/content/indices/{f}",
    "https://archives.nseindia.com/content/indices/{f}",
    "https://www.niftyindices.com/IndexConstituent/{f}",
]
SMALL_SAMPLE_EVERY = 4          # every 4th of 100 -> 25 stocks
SMALL_SAMPLE_SIZE = 25          # the small-cap group is frozen at this size


def download(fname: str) -> pd.DataFrame | None:
    for m in MIRRORS:
        url = m.format(f=fname)
        try:
            req = urllib.request.Request(url, headers={**HEADERS, "Referer": "https://www.nseindia.com/"})
            with urllib.request.urlopen(req, timeout=25) as r:
                raw = r.read()
            df = parse(raw)
            if df is not None and len(df) >= 20:
                print(f"{fname}: {len(df)} stocks from {url}")
                return df
            print(f"{fname}: unexpected content from {url}")
        except Exception as exc:
            print(f"{fname}: {url} failed ({type(exc).__name__}: {exc})")
    return None


def parse(raw: bytes) -> pd.DataFrame | None:
    df = pd.read_csv(io.BytesIO(raw), dtype=str)
    df.columns = [c.strip() for c in df.columns]
    need = {"Company Name", "Industry", "Symbol", "ISIN Code"}
    if not need <= set(df.columns):
        return None
    df = df.apply(lambda s: s.str.strip() if s.dtype == object else s)
    df["Industry"] = df["Industry"].fillna("").replace("", "Other")
    df["ISIN Code"] = df["ISIN Code"].where(df["ISIN Code"].notna() & (df["ISIN Code"] != ""), None)
    df["Company Name"] = df["Company Name"].fillna(df["Symbol"])
    if "Series" in df:
        df = df[df["Series"].isin(["EQ", "BE"]) | df["Series"].isna()]
    return df.dropna(subset=["Symbol"]).drop_duplicates("Symbol")


def select(index: str, df: pd.DataFrame) -> pd.DataFrame:
    if index == "NIFTY SMALLCAP 100":
        df = df.sort_values("Symbol").reset_index(drop=True)
        return df.iloc[::SMALL_SAMPLE_EVERY]
    return df


UPSERT = """
WITH sec AS (
    INSERT INTO sectors (sector_name) VALUES (%(industry)s)
    ON CONFLICT (sector_name) DO UPDATE SET sector_name = EXCLUDED.sector_name
    RETURNING sector_id
)
INSERT INTO companies (ticker, company_name, sector_id, hq_city_id, in_dashboard, cap_group, index_membership, isin)
SELECT %(ticker)s, %(name)s, sec.sector_id, NULL, FALSE, %(cap)s, ARRAY[%(index)s]::text[], %(isin)s FROM sec
ON CONFLICT (ticker) DO UPDATE SET
    -- an existing (e.g. dashboard) stock keeps its name, sector, map position and dashboard flag;
    -- only the membership list grows and a missing ISIN is filled in
    index_membership = (SELECT ARRAY(SELECT DISTINCT unnest(companies.index_membership || EXCLUDED.index_membership))),
    isin = COALESCE(companies.isin, EXCLUDED.isin)
RETURNING (xmax = 0) AS inserted
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", nargs=2, action="append", metavar=("INDEX", "PATH"),
                    help="use a constituent CSV downloaded by hand")
    args = ap.parse_args()
    manual = {k.upper(): v for k, v in (args.file or [])}

    with get_conn() as conn:
        with pipeline_run(conn, "universe") as run:
            added, seen, missing = 0, 0, []
            known = {r[0] for r in conn.execute("SELECT ticker FROM companies")}
            n_small = conn.execute("SELECT COUNT(*) FROM companies WHERE is_active AND cap_group = 'small'").fetchone()[0]
            for index, fname in INDEXES.items():
                df = parse(open(manual[index], "rb").read()) if index in manual else download(fname)
                if df is None:
                    missing.append(index)
                    continue
                cap = "small" if "SMALLCAP" in index else "large"
                for _, r in select(index, df).iterrows():
                    ticker = f"{r['Symbol']}.NS"
                    if cap == "small" and ticker not in known:
                        if n_small >= SMALL_SAMPLE_SIZE:      # sample frozen: never grow it
                            continue
                        n_small += 1
                    res = conn.execute(UPSERT, dict(ticker=ticker, name=str(r["Company Name"])[:120],
                                                    industry=str(r["Industry"])[:50], cap=cap,
                                                    index=index, isin=r["ISIN Code"])).fetchone()
                    known.add(ticker)
                    seen += 1
                    added += int(bool(res and res[0]))
                conn.commit()
            n = conn.execute("""SELECT COUNT(*) FILTER (WHERE in_dashboard), COUNT(*) FILTER (WHERE NOT in_dashboard),
                                       COUNT(*) FILTER (WHERE cap_group = 'small')
                                FROM companies WHERE is_active""").fetchone()
            run["rows_upserted"] = added
            run["tickers_failed"] = missing
            run["message"] = (f"{seen} index members checked, {added} new research stocks; universe now "
                              f"{n[0]} dashboard + {n[1]} research ({n[2]} small caps)"
                              + (f"; could not download: {', '.join(missing)}" if missing else ""))
            print(run["message"])


if __name__ == "__main__":
    main()

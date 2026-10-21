"""Public Yahoo OHLC for the 25 dashboard stocks (raw prices: keep the output out of git): python task1_fetch_ohlc.py out.csv
Yahoo sometimes returns nothing for a ticker (TCS, INFY did once): re-run that ticker."""
import re, sys
from pathlib import Path
import pandas as pd, yfinance as yf
seed = open(str(Path(__file__).resolve().parents[2] / "sql" / "02_seed.sql")).read()
rows = re.findall(r"\('([A-Z&\-]+\.NS)',\s+'(?:[^']|'')+',\s+'(\w+)',", seed)
assert len(rows) == 25, len(rows)
frames = []
for t, sec in rows:
    df = yf.Ticker(t).history(start="2015-01-01", end="2026-10-21", interval="1d", auto_adjust=False, actions=False)
    df = df.reset_index()
    df["price_date"] = pd.to_datetime(df["Date"].astype(str).str[:10]).dt.date
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Adj Close": "adj_close", "Volume": "volume"})
    df["ticker"], df["sector"] = t, sec
    frames.append(df[["ticker", "sector", "price_date", "open", "high", "low", "close", "adj_close", "volume"]])
    print(t, sec, len(df), df.price_date.min(), df.price_date.max(), flush=True)
pd.concat(frames).to_csv(sys.argv[1], index=False)

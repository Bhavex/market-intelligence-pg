import hashlib
import io
import zipfile
from datetime import date

import pandas as pd

PARSER_VERSION = "bhavcopy-parser-v1"
LEGACY = "legacy_cm"
UDIFF = "udiff_cm"
UDIFF_START = date(2024, 7, 8)

LEGACY_REQUIRED = {"SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE", "TOTTRDQTY", "TIMESTAMP", "ISIN"}
UDIFF_REQUIRED = {"TradDt", "Sgmt", "FinInstrmTp", "ISIN", "TckrSymb", "SctySrs", "OpnPric", "HghPric", "LwPric",
                  "ClsPric", "LastPric", "PrvsClsgPric", "TtlTradgVol"}

COLUMNS = ["trade_date", "symbol", "series", "isin", "open", "high", "low", "close", "last", "prev_close", "volume",
           "turnover", "trades", "format"]


class BhavcopyFormatError(ValueError):
    pass


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def unwrap(raw: bytes) -> bytes:
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise BhavcopyFormatError("zip archive contains no CSV file")
            return z.read(names[0])
    return raw


def _header(text: str) -> set:
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return {c.strip() for c in first.split(",") if c.strip()}


def detect_format(raw: bytes) -> str:
    text = unwrap(raw).decode("utf-8", "replace")
    cols = _header(text)
    if UDIFF_REQUIRED <= cols:
        return UDIFF
    if LEGACY_REQUIRED <= cols:
        return LEGACY
    raise BhavcopyFormatError(f"unrecognised bhavcopy header: {sorted(cols)[:8]}")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.strip().replace({"": None, "-": None}), errors="coerce")


def parse_legacy(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(unwrap(raw)), dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    missing = LEGACY_REQUIRED - set(df.columns)
    if missing:
        raise BhavcopyFormatError(f"legacy CM bhavcopy is missing columns {sorted(missing)}")
    df = df.loc[:, [c for c in df.columns if c and not c.startswith("Unnamed")]]
    out = pd.DataFrame({
        "trade_date": pd.to_datetime(df["TIMESTAMP"].str.strip(), format="%d-%b-%Y").dt.date,
        "symbol": df["SYMBOL"].str.strip(), "series": df["SERIES"].str.strip(),
        "isin": df["ISIN"].str.strip().replace({"": None}),
        "open": _num(df["OPEN"]), "high": _num(df["HIGH"]), "low": _num(df["LOW"]), "close": _num(df["CLOSE"]),
        "last": _num(df["LAST"]), "prev_close": _num(df["PREVCLOSE"]), "volume": _num(df["TOTTRDQTY"]),
        "turnover": _num(df["TOTTRDVAL"]) if "TOTTRDVAL" in df else None,
        "trades": _num(df["TOTALTRADES"]) if "TOTALTRADES" in df else None,
    })
    out["format"] = LEGACY
    return _finish(out)


def parse_udiff(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(unwrap(raw)), dtype=str, keep_default_na=False)
    df.columns = [c.strip().lstrip("﻿") for c in df.columns]
    missing = UDIFF_REQUIRED - set(df.columns)
    if missing:
        raise BhavcopyFormatError(f"UDiFF CM bhavcopy is missing columns {sorted(missing)}")
    df = df[(df["Sgmt"].str.strip() == "CM") & (df["FinInstrmTp"].str.strip() == "STK")]
    out = pd.DataFrame({
        "trade_date": pd.to_datetime(df["TradDt"].str.strip(), format="%Y-%m-%d").dt.date,
        "symbol": df["TckrSymb"].str.strip(), "series": df["SctySrs"].str.strip(),
        "isin": df["ISIN"].str.strip().replace({"": None}),
        "open": _num(df["OpnPric"]), "high": _num(df["HghPric"]), "low": _num(df["LwPric"]), "close": _num(df["ClsPric"]),
        "last": _num(df["LastPric"]), "prev_close": _num(df["PrvsClsgPric"]), "volume": _num(df["TtlTradgVol"]),
        "turnover": _num(df["TtlTrfVal"]) if "TtlTrfVal" in df else None,
        "trades": _num(df["TtlNbOfTxsExctd"]) if "TtlNbOfTxsExctd" in df else None,
    })
    out["format"] = UDIFF
    return _finish(out)


def _finish(out: pd.DataFrame) -> pd.DataFrame:
    out = out[out["symbol"].astype(bool) & out["close"].notna()]
    out = out.drop_duplicates(["trade_date", "symbol", "series"], keep="first")
    return out[COLUMNS].reset_index(drop=True)


def parse(raw: bytes) -> pd.DataFrame:
    return parse_udiff(raw) if detect_format(raw) == UDIFF else parse_legacy(raw)


def expected_format(day: date) -> str:
    return UDIFF if day >= UDIFF_START else LEGACY


def url(day: date, fmt: str | None = None) -> str:
    fmt = fmt or expected_format(day)
    if fmt == UDIFF:
        return f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    mon = day.strftime("%b").upper()
    return f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{day:%Y}/{mon}/cm{day:%d}{mon}{day:%Y}bhav.csv.zip"

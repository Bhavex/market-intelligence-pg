import io
import os
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import bhavcopy as BC
import reconcile as R

TEST_DB = os.environ.get("TEST_DATABASE_URL")
FIX = Path(__file__).resolve().parent / "fixtures" / "bhavcopy"
LEGACY_FILE = FIX / "cm04JAN2024bhav.csv"
UDIFF_FILE = FIX / "BhavCopy_NSE_CM_0_0_0_20241104_F_0000.csv"


def zipped(path: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(path.name, path.read_bytes())
    return buf.getvalue()


class TestParsers:
    @pytest.mark.parametrize("path,fmt", [(LEGACY_FILE, BC.LEGACY), (UDIFF_FILE, BC.UDIFF)])
    def test_format_is_detected_from_the_header_plain_or_zipped(self, path, fmt):
        assert BC.detect_format(path.read_bytes()) == fmt
        assert BC.detect_format(zipped(path)) == fmt

    def test_legacy_rows(self):
        df = BC.parse_legacy(LEGACY_FILE.read_bytes())
        assert list(df.columns) == BC.COLUMNS
        assert set(df["trade_date"]) == {date(2024, 1, 4)}
        assert len(df) == 6
        infy = df[(df.symbol == "INFY") & (df.series == "EQ")].iloc[0]
        assert (infy["close"], infy["last"], infy["prev_close"], infy["volume"], infy["isin"]) == (
            1510.25, 1511.0, 1495.6, 5123456, "INE009A01021")
        assert {"EQ", "BL"} <= set(df[df.symbol == "INFY"]["series"])
        assert "M&M" in set(df.symbol)

    def test_udiff_rows_keep_only_cash_market_stocks(self):
        df = BC.parse_udiff(UDIFF_FILE.read_bytes())
        assert set(df["trade_date"]) == {date(2024, 11, 4)} and "NIFTYBEES" not in set(df.symbol)
        tcs = df[df.symbol == "TCS"].iloc[0]
        assert (tcs["series"], tcs["close"], tcs["last"], tcs["prev_close"], tcs["low"], tcs["high"]) == (
            "EQ", 4125.6, 4127.5, 4098.7, 4080.0, 4150.0)

    def test_both_parsers_produce_the_same_shape(self):
        a, b = BC.parse(LEGACY_FILE.read_bytes()), BC.parse(UDIFF_FILE.read_bytes())
        assert list(a.columns) == list(b.columns)
        assert a["close"].dtype.kind == b["close"].dtype.kind == "f"

    def test_unknown_or_truncated_files_are_refused(self):
        with pytest.raises(BC.BhavcopyFormatError):
            BC.parse(b"<html>blocked</html>")
        with pytest.raises(BC.BhavcopyFormatError):
            BC.parse_legacy(b"SYMBOL,SERIES,CLOSE\nINFY,EQ,1\n")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("notes.txt", "no csv here")
        with pytest.raises(BC.BhavcopyFormatError):
            BC.unwrap(buf.getvalue())

    def test_one_bp_or_one_tick_counts_as_agreement(self):
        assert R.classify(4126.0, {"close": 4125.6, "last": 4127.5, "prev_close": 4098.7, "low": 4080, "high": 4150}).classification == "match"

    def test_urls_and_expected_format_follow_the_switch_date(self):
        assert BC.expected_format(date(2024, 7, 5)) == BC.LEGACY and BC.expected_format(date(2024, 7, 8)) == BC.UDIFF
        assert BC.url(date(2024, 1, 4)).endswith("/historical/EQUITIES/2024/JAN/cm04JAN2024bhav.csv.zip")
        assert BC.url(date(2024, 11, 4)).endswith("BhavCopy_NSE_CM_0_0_0_20241104_F_0000.csv.zip")

    def test_raw_bytes_hash_changes_with_any_byte(self):
        raw = UDIFF_FILE.read_bytes()
        assert BC.sha256(raw) != BC.sha256(raw.replace(b"4125.60", b"4125.65"))


BHAV = {"close": 100.0, "last": 100.4, "prev_close": 98.0, "low": 97.0, "high": 103.0}


class TestClassify:
    @pytest.mark.parametrize("yahoo,expected", [
        (100.0, "match"), (100.04, "match"), (100.4, "last_traded_not_close"), (98.0, "previous_session_close"),
        (50.0, "split_or_bonus_basis"), (10.0, "split_or_bonus_basis"), (101.5, "intraday_value"),
        (120.0, "unexplained"), (None, "missing_in_yahoo"),
    ])
    def test_each_disagreement_class(self, yahoo, expected):
        assert R.classify(yahoo, BHAV).classification == expected

    def test_missing_bhavcopy_row(self):
        assert R.classify(100.0, None).classification == "missing_in_bhavcopy"

    def test_difference_is_reported_in_basis_points(self):
        assert R.classify(101.5, BHAV).diff_bp == pytest.approx(150.0)


@pytest.fixture
def conn():
    import psycopg
    c = psycopg.connect(TEST_DB)
    yield c
    c.rollback()
    c.close()


def put(conn, ticker, day, close, volume=1000):
    conn.execute("DELETE FROM daily_prices WHERE ticker = %s AND price_date = %s", (ticker, day))
    if close is not None:
        conn.execute("""INSERT INTO daily_prices (ticker, price_date, open, high, low, close, adj_close, volume)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""", (ticker, day, close, close, close, close, close, volume))


@pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")
class TestReconcileDatabase:
    @pytest.mark.parametrize("path,day", [(LEGACY_FILE, date(2024, 1, 4)), (UDIFF_FILE, date(2024, 11, 4))])
    def test_classifies_each_instrument_on_matching_symbol_series_and_date(self, conn, path, day):
        bhav = BC.parse(path.read_bytes()).set_index(["symbol", "series"])
        conn.execute("DELETE FROM daily_prices WHERE price_date = %s", (day,))
        put(conn, "INFY.NS", day, float(bhav.loc[("INFY", "EQ"), "close"]))
        put(conn, "TCS.NS", day, float(bhav.loc[("TCS", "EQ"), "last"]))
        put(conn, "M&M.NS", day, round(float(bhav.loc[("M&M", "EQ"), "close"]) / 2, 2))
        put(conn, "WIPRO.NS", day, 500.0)
        results = R.reconcile_bytes(conn, path.read_bytes(), day, str(path))
        got = {r["ticker"]: r["classification"] for r in results}
        assert got == {"INFY.NS": "match", "TCS.NS": "last_traded_not_close", "M&M.NS": "split_or_bonus_basis",
                       "HDFCBANK.NS": "missing_in_yahoo", "WIPRO.NS": "missing_in_bhavcopy"}
        infy = next(r for r in results if r["ticker"] == "INFY.NS")
        assert infy["series"] == "EQ" and infy["matched_by"] == "symbol_series"
        assert infy["bhav_close_basis"] == "unadjusted" and infy["bhav_format"] in (BC.LEGACY, BC.UDIFF)
        stored = conn.execute("""SELECT COUNT(*) FROM reconciliation_results WHERE trade_date = %s""", (day,)).fetchone()[0]
        assert stored == 5
        led = conn.execute("""SELECT hash_basis, response_sha256, parser_version FROM fetch_log
                              WHERE endpoint LIKE 'bhavcopy:%%' ORDER BY fetch_id DESC LIMIT 1""").fetchone()
        assert led == ("raw_bytes", BC.sha256(path.read_bytes()), BC.PARSER_VERSION)

    def test_isin_is_used_when_the_symbol_does_not_match(self, conn):
        day = date(2024, 11, 4)
        conn.execute("UPDATE companies SET isin = 'INE009A01021' WHERE ticker = 'INFY.NS'")
        conn.execute("UPDATE instruments SET isin = 'INE009A01021' WHERE instrument_id = (SELECT instrument_id FROM companies WHERE ticker = 'INFY.NS')")
        raw = UDIFF_FILE.read_bytes().replace(b",INFY,EQ,", b",INFOSYS,EQ,")
        conn.execute("DELETE FROM daily_prices WHERE price_date = %s", (day,))
        put(conn, "INFY.NS", day, 1820.35)
        got = {r["ticker"]: (r["classification"], r["matched_by"]) for r in R.reconcile_bytes(conn, raw, day)}
        assert got["INFY.NS"] == ("match", "isin")

    def test_results_are_append_only(self, conn):
        import psycopg
        day = date(2024, 1, 4)
        put(conn, "INFY.NS", day, 1510.25)
        R.reconcile_bytes(conn, LEGACY_FILE.read_bytes(), day)
        with pytest.raises(psycopg.errors.RestrictViolation):
            with conn.transaction():
                conn.execute("DELETE FROM reconciliation_results WHERE trade_date = %s", (day,))

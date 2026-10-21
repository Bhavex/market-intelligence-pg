"""Tests for the price pipeline. Run:  python -m unittest discover -s tests -v

Pure tests always run. Database tests run only if TEST_DATABASE_URL is set (a throwaway/local
database with the repo schema applied); they run inside a transaction that is rolled back, and they
never read DATABASE_URL, so they cannot touch production by accident."""
import os
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import prices  # noqa: E402
from prices import IST, UnfinishedSessionError, _session_finished, _to_rows, guard_final_sessions  # noqa: E402

TEST_DB = os.environ.get("TEST_DATABASE_URL")


def ist(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


class SessionFinished(unittest.TestCase):
    def test_today_is_held_until_1630_ist(self):
        self.assertFalse(_session_finished(date(2026, 10, 13), ist(2026, 10, 13, 9, 40)))    # the 09:39 run that stored 11,320
        self.assertFalse(_session_finished(date(2026, 10, 13), ist(2026, 10, 13, 15, 31)))   # just after the close
        self.assertFalse(_session_finished(date(2026, 10, 13), ist(2026, 10, 13, 16, 29)))
        self.assertTrue(_session_finished(date(2026, 10, 13), ist(2026, 10, 13, 16, 30)))
        self.assertTrue(_session_finished(date(2026, 10, 13), ist(2026, 10, 13, 23, 5)))

    def test_previous_days_are_final(self):
        self.assertTrue(_session_finished(date(2026, 10, 12), ist(2026, 10, 13, 0, 5)))

    def test_clock_in_other_timezone_is_converted(self):
        utc_1059 = datetime(2026, 10, 13, 10, 59, tzinfo=timezone.utc)          # 16:29 IST
        self.assertFalse(_session_finished(date(2026, 10, 13), utc_1059))
        self.assertTrue(_session_finished(date(2026, 10, 13), utc_1059 + timedelta(minutes=1)))
        late_utc = datetime(2026, 10, 13, 20, 0, tzinfo=timezone.utc)           # 01:30 IST on the 14th
        self.assertTrue(_session_finished(date(2026, 10, 13), late_utc))


class ToRows(unittest.TestCase):
    @staticmethod
    def frame():
        idx = pd.DatetimeIndex(["2026-10-09", "2026-10-12", "2026-10-13"], tz="Asia/Kolkata")
        return pd.DataFrame({"Open": [1.0, 2.0, 3.0], "High": [2.0, 3.0, 4.0], "Low": [0.5, 1.0, 2.0],
                             "Close": [1.5, 2.5, 3.5], "Adj Close": [1.5, 2.5, 3.5], "Volume": [10, 20, 30]}, index=idx)

    def test_unfinished_bar_is_not_stored_but_counted(self):
        original = prices._now
        try:
            prices._now = lambda: ist(2026, 10, 13, 9, 40)
            rows, held = _to_rows("X.NS", self.frame())
            self.assertEqual(sorted(rows), [date(2026, 10, 9), date(2026, 10, 12)])
            self.assertEqual(held, 1)
            prices._now = lambda: ist(2026, 10, 13, 17, 10)
            rows, held = _to_rows("X.NS", self.frame())
            self.assertEqual(sorted(rows), [date(2026, 10, 9), date(2026, 10, 12), date(2026, 10, 13)])
            self.assertEqual(held, 0)
        finally:
            prices._now = original


class GuardFinalSessions(unittest.TestCase):
    """The 13 Oct 2026 09:39 IST load stored live quotes as closes; the guard must refuse that."""

    @staticmethod
    def row(day, close=11320.0):
        return ("MARUTI.NS", day, close, close, close, close, close, 100)

    def test_refuses_todays_row_before_1630(self):
        for when in (ist(2026, 10, 13, 9, 39), ist(2026, 10, 13, 15, 31), ist(2026, 10, 13, 16, 29)):
            with self.assertRaises(UnfinishedSessionError):
                guard_final_sessions([self.row(date(2026, 10, 13))], now=when)

    def test_refuses_the_whole_batch_if_one_row_is_unfinished(self):
        rows = [self.row(date(2026, 10, 12)), self.row(date(2026, 10, 13))]
        with self.assertRaises(UnfinishedSessionError) as cm:
            guard_final_sessions(rows, now=ist(2026, 10, 13, 9, 39))
        self.assertIn("2026-10-13", str(cm.exception))
        self.assertNotIn("2026-10-12", str(cm.exception))

    def test_refuses_future_date(self):
        with self.assertRaises(UnfinishedSessionError):
            guard_final_sessions([self.row(date(2026, 10, 14))], now=ist(2026, 10, 13, 23, 0))

    def test_allows_todays_row_from_1630_and_earlier_days_any_time(self):
        guard_final_sessions([self.row(date(2026, 10, 13))], now=ist(2026, 10, 13, 16, 30))
        guard_final_sessions([self.row(date(2026, 10, 12))], now=ist(2026, 10, 13, 9, 39))

    def test_replay_of_the_13_oct_run_25_rows_at_0409_utc(self):
        """Run 38345084319 (workflow_dispatch) started 04:09 UTC = 09:39 IST and wrote 25 rows for 13 Oct.
        The runner clock is UTC; the guard must convert it, whatever triggered the job."""
        tickers = [f"T{i:02d}.NS" for i in range(25)]
        rows = [(t, date(2026, 10, 13), 1.0, 2.0, 0.5, 1.5, 1.5, 100) for t in tickers]
        at_run = datetime(2026, 10, 13, 4, 9, 25, tzinfo=timezone.utc)
        with self.assertRaises(UnfinishedSessionError) as cm:
            guard_final_sessions(rows, now=at_run)
        self.assertIn("1 unfinished session", str(cm.exception))

    def test_cutoff_is_1630_ist_whatever_timezone_the_clock_is_in(self):
        row = [self.row(date(2026, 10, 13))]
        utc = timezone.utc
        with self.assertRaises(UnfinishedSessionError):
            guard_final_sessions(row, now=datetime(2026, 10, 13, 10, 59, 59, tzinfo=utc))   # 16:29:59 IST
        guard_final_sessions(row, now=datetime(2026, 10, 13, 11, 0, 0, tzinfo=utc))          # 16:30:00 IST
        # 23:00 UTC on 12 Oct is already 04:30 IST on 13 Oct: the 13 Oct row is not final, the 12 Oct row is
        with self.assertRaises(UnfinishedSessionError):
            guard_final_sessions(row, now=datetime(2026, 10, 12, 23, 0, tzinfo=utc))
        guard_final_sessions([self.row(date(2026, 10, 12))], now=datetime(2026, 10, 12, 23, 0, tzinfo=utc))

    def test_explicit_intraday_mark_is_allowed(self):
        guard_final_sessions([self.row(date(2026, 10, 13))], intraday=True, now=ist(2026, 10, 13, 9, 39))

    def test_upsert_rows_checks_the_guard_before_touching_the_database(self):
        original = prices._now
        try:
            prices._now = lambda: ist(2026, 10, 13, 9, 39)
            for kwargs in ({}, {"insert_only": True}):
                with self.assertRaises(UnfinishedSessionError):
                    prices.upsert_rows(None, [self.row(date(2026, 10, 13))], **kwargs)   # conn=None: never reached
        finally:
            prices._now = original


@unittest.skipUnless(TEST_DB, "set TEST_DATABASE_URL to run database tests")
class Database(unittest.TestCase):
    TICKER = "TCS.NS"

    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(TEST_DB)
        self.day = date(1999, 1, 4)
        self.conn.execute("DELETE FROM daily_prices WHERE ticker = %s AND price_date = %s", (self.TICKER, self.day))

    def tearDown(self):
        self.conn.rollback()                      # nothing the tests write is ever committed
        self.conn.close()

    def row(self, close, volume=100):
        return (self.TICKER, self.day, close, close + 1, close - 1, close, close, volume)

    def stored(self):
        return self.conn.execute("SELECT close, volume FROM daily_prices WHERE ticker = %s AND price_date = %s",
                                 (self.TICKER, self.day)).fetchone()

    def test_insert_unchanged_update_counts(self):
        s = prices.upsert_rows(self.conn, [self.row(11320)], commit=False)
        self.assertEqual((s.inserted, s.updated, s.unchanged), (1, 0, 0))
        s = prices.upsert_rows(self.conn, [self.row(11320)], commit=False)
        self.assertEqual((s.inserted, s.updated, s.unchanged), (0, 0, 1))
        s = prices.upsert_rows(self.conn, [self.row(11245, 761480)], commit=False)      # the MARUTI 13 Oct heal
        self.assertEqual((s.inserted, s.updated, s.unchanged), (0, 1, 0))
        self.assertEqual(s.updated_dates, (self.day,))
        self.assertEqual(tuple(map(float, self.stored())), (11245.0, 761480.0))

    def test_unchanged_row_is_not_rewritten(self):
        prices.upsert_rows(self.conn, [self.row(100)], commit=False)
        t1 = self.conn.execute("SELECT loaded_at FROM daily_prices WHERE ticker=%s AND price_date=%s", (self.TICKER, self.day)).fetchone()[0]
        prices.upsert_rows(self.conn, [self.row(100)], commit=False)
        t2 = self.conn.execute("SELECT loaded_at FROM daily_prices WHERE ticker=%s AND price_date=%s", (self.TICKER, self.day)).fetchone()[0]
        self.assertEqual(t1, t2)

    def test_insert_only_never_overwrites(self):
        prices.upsert_rows(self.conn, [self.row(11315, 323997)], commit=False)           # official row
        s = prices.upsert_rows(self.conn, [self.row(11316, 268006)], insert_only=True, commit=False)   # hourly approximation
        self.assertEqual((s.inserted, s.updated, s.unchanged), (0, 0, 1))
        self.assertEqual(tuple(map(float, self.stored())), (11315.0, 323997.0))
        self.conn.execute("DELETE FROM daily_prices WHERE ticker = %s AND price_date = %s", (self.TICKER, self.day))
        s = prices.upsert_rows(self.conn, [self.row(11316, 268006)], insert_only=True, commit=False)
        self.assertEqual(s.inserted, 1)

    def test_heal_window_is_five_trading_sessions(self):
        from daily_update import HEAL_START_SQL

        def start(at):
            return self.conn.execute(HEAL_START_SQL, {"at": at, "skip": 4}).fetchone()[0]
        self.assertEqual(start("2026-10-13 18:00+05:30"), date(2026, 10, 7))    # 13, 12, 9, 8, 7
        self.assertEqual(start("2026-10-13 10:00+05:30"), date(2026, 10, 6))    # today not final: 12, 9, 8, 7, 6
        self.assertEqual(start("2026-10-05 18:00+05:30"), date(2026, 9, 28))    # skips 2 Oct holiday and the weekend


if __name__ == "__main__":
    unittest.main()

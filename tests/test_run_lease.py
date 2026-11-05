import pytest

import run_lease


class FakeLeaseConn:
    def __init__(self, answers):
        self.answers = list(answers)
        self.sql = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.sql.append((" ".join(sql.split()), params))
        conn = self

        class R:
            def fetchone(self):
                return conn.answers.pop(0) if conn.answers else None

        return R()

    def commit(self):
        self.commits += 1


class TestLeaseLogic:
    def test_waits_while_another_run_holds_the_lease_then_takes_it(self):
        conn = FakeLeaseConn([(False, 4, "backfill", "t0", "t1"), (False, 4, "backfill", "t0", "t1"),
                              (True, 9, "daily_update", "t2", "t3")])
        naps = []
        h = run_lease.acquire(conn, 9, "daily_update", wait=60, ttl=30, sleep=naps.append)
        assert (h.run_id, h.lease, h.ttl) == (9, "pipeline", 30) and len(naps) == 2

    def test_gives_up_and_logs_a_refusal_when_the_wait_runs_out(self):
        conn = FakeLeaseConn([(False, 4, "backfill", "t0", "t1")])
        with pytest.raises(run_lease.RunLeaseBusy, match="held by run 4 \\(backfill\\)"):
            run_lease.acquire(conn, 9, "daily_update", wait=0, sleep=lambda s: None)
        assert any("'refused'" in s for s, _ in conn.sql)

    def test_fence_stops_a_run_whose_lease_was_lost(self):
        h = run_lease.Holding("pipeline", 9, 30)
        run_lease.HELD["holding"] = h
        try:
            run_lease.fence(FakeLeaseConn([(1,)]))
            with pytest.raises(run_lease.LeaseLost):
                run_lease.fence(FakeLeaseConn([None]))
            assert h.lost.is_set()
            with pytest.raises(run_lease.LeaseLost):
                run_lease.fence(FakeLeaseConn([(1,)]))
        finally:
            run_lease.HELD["holding"] = None

    def test_no_fence_outside_a_leased_run(self):
        conn = FakeLeaseConn([])
        run_lease.fence(conn)
        assert conn.sql == []

    def test_lease_lost_is_not_swallowed_by_per_ticker_error_handling(self):
        assert not issubclass(run_lease.LeaseLost, Exception)

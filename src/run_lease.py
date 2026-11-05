import argparse
import logging
import os
import threading
import time
from dataclasses import dataclass, field

LEASE = "pipeline"
log = logging.getLogger("market.lease")


def _seconds(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def ttl_s() -> float:
    return _seconds("PIPELINE_LEASE_TTL_S", 300)


def wait_s() -> float:
    return _seconds("PIPELINE_LEASE_WAIT_S", 600)


def heartbeat_s() -> float:
    return _seconds("PIPELINE_HEARTBEAT_S", 60)


def stale_after_s() -> float:
    return _seconds("PIPELINE_STALE_AFTER_S", 1800)


def poll_s() -> float:
    return _seconds("PIPELINE_LEASE_POLL_S", 5)


class RunLeaseBusy(RuntimeError):
    def __init__(self, lease, holder_run, holder_job, since, expires):
        super().__init__(f"lease '{lease}' is held by run {holder_run} ({holder_job}) since {since}, "
                         f"expires {expires}; refusing to start an overlapping run")
        self.holder_run = holder_run


class LeaseLost(SystemExit):
    def __init__(self, lease, run_id):
        super().__init__(f"run {run_id} no longer holds lease '{lease}'; stopping before any further write")


@dataclass
class Holding:
    lease: str
    run_id: int
    ttl: float
    lost: threading.Event = field(default_factory=threading.Event)
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


HELD = {"holding": None}


def available(conn) -> bool:
    row = conn.execute("SELECT to_regclass('public.run_leases')::text").fetchone()
    return bool(row) and row[0] == "run_leases"


def reap(conn, by_run=None, stale_after=None) -> list:
    rows = conn.execute("SELECT run_id, job_name, last_seen FROM reap_stale_runs(make_interval(secs => %s), %s)",
                        (stale_after_s() if stale_after is None else stale_after, by_run)).fetchall()
    conn.commit()
    for run_id, job, seen in rows:
        log.warning("marked stale run %s (%s) failed: no heartbeat since %s", run_id, job, seen)
    return rows


def try_acquire(conn, run_id: int, job: str, lease: str = LEASE, ttl: float | None = None):
    row = conn.execute("SELECT * FROM acquire_run_lease(%s, %s, %s, make_interval(secs => %s))",
                       (lease, run_id, job, ttl_s() if ttl is None else ttl)).fetchone()
    conn.commit()
    return row


def acquire(conn, run_id: int, job: str, lease: str = LEASE, wait: float | None = None,
            ttl: float | None = None, sleep=time.sleep) -> Holding:
    deadline = time.monotonic() + (wait_s() if wait is None else wait)
    while True:
        acquired, holder, holder_job, since, expires = try_acquire(conn, run_id, job, lease, ttl)
        if acquired:
            return Holding(lease, run_id, ttl_s() if ttl is None else ttl)
        if time.monotonic() >= deadline:
            conn.execute("INSERT INTO run_lease_log (lease_name, run_id, event, detail) VALUES (%s, %s, 'refused', %s)",
                         (lease, run_id, f"held by run {holder} ({holder_job})"))
            conn.commit()
            raise RunLeaseBusy(lease, holder, holder_job, since, expires)
        log.info("lease '%s' held by run %s (%s); waiting", lease, holder, holder_job)
        sleep(min(poll_s(), max(0.0, deadline - time.monotonic())))


def renew(conn, holding: Holding) -> bool:
    ok = conn.execute("SELECT renew_run_lease(%s, %s, make_interval(secs => %s))",
                      (holding.lease, holding.run_id, holding.ttl)).fetchone()[0]
    conn.commit()
    return bool(ok)


def release(conn, holding: Holding) -> bool:
    ok = conn.execute("SELECT release_run_lease(%s, %s)", (holding.lease, holding.run_id)).fetchone()[0]
    conn.commit()
    return bool(ok)


def _beat(holding: Holding, connect, interval: float) -> None:
    try:
        conn = connect()
    except BaseException as exc:
        log.warning("heartbeat could not connect: %s", exc)
        return
    try:
        while not holding.stop.wait(interval):
            try:
                if not renew(conn, holding):
                    holding.lost.set()
                    log.error("run %s lost lease '%s'", holding.run_id, holding.lease)
                    return
            except Exception as exc:
                log.warning("heartbeat failed: %s", exc)
                try:
                    conn.rollback()
                except Exception:
                    pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def start_heartbeat(holding: Holding, connect, interval: float | None = None) -> Holding:
    t = threading.Thread(target=_beat, args=(holding, connect, heartbeat_s() if interval is None else interval),
                         name=f"lease-heartbeat-{holding.run_id}", daemon=True)
    holding.thread = t
    t.start()
    return holding


def stop_heartbeat(holding: Holding) -> None:
    holding.stop.set()
    if holding.thread is not None:
        holding.thread.join(timeout=10)


def fence(conn) -> None:
    holding = HELD["holding"]
    if holding is None:
        return
    if holding.lost.is_set():
        raise LeaseLost(holding.lease, holding.run_id)
    row = conn.execute("""SELECT 1 FROM run_leases
                          WHERE lease_name = %s AND run_id = %s AND expires_at > clock_timestamp()
                          FOR SHARE""", (holding.lease, holding.run_id)).fetchone()
    if not row:
        holding.lost.set()
        raise LeaseLost(holding.lease, holding.run_id)


def main(argv=None) -> int:
    from db import get_conn
    ap = argparse.ArgumentParser(description="Mark pipeline runs with no heartbeat as failed and show the lease.")
    ap.add_argument("--stale-after", type=float, default=None, help="seconds without a heartbeat (default 1800)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    with get_conn() as conn:
        reaped = reap(conn, None, args.stale_after)
        print(f"stale runs marked failed: {len(reaped)}")
        for run_id, job, seen in reaped:
            print(f"  run {run_id} ({job}), last heartbeat {seen}")
        for row in conn.execute("SELECT lease_name, run_id, job_name, expires_at, is_live FROM v_run_leases"):
            print(f"lease {row[0]}: run {row[1]} ({row[2]}), expires {row[3]}, live {row[4]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

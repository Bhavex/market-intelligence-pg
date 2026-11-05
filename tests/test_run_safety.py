import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import run_lease

TEST_DB = os.environ.get("TEST_DATABASE_URL")
ROOT = Path(__file__).resolve().parent.parent
WORKER = Path(__file__).resolve().parent / "crash_worker.py"
TICKERS = ["INFY.NS", "TCS.NS", "WIPRO.NS"]
pytestmark = pytest.mark.skipif(not TEST_DB, reason="needs TEST_DATABASE_URL")


def dsn(dbname):
    import psycopg
    params = psycopg.conninfo.conninfo_to_dict(TEST_DB)
    params["dbname"] = dbname
    return psycopg.conninfo.make_conninfo(**params)


def admin():
    import psycopg
    return psycopg.connect(TEST_DB, autocommit=True)


@pytest.fixture(scope="module")
def template():
    import psycopg
    name = psycopg.conninfo.conninfo_to_dict(TEST_DB)["dbname"] + "_crash_tmpl"
    with admin() as a:
        try:
            a.execute(f'DROP DATABASE IF EXISTS "{name}"')
            a.execute(f'CREATE DATABASE "{name}"')
        except psycopg.Error as exc:
            pytest.skip(f"cannot create scratch databases: {exc}")
    env = {**os.environ, "DATABASE_URL": dsn(name)}
    subprocess.run([sys.executable, str(ROOT / "src" / "setup_db.py")], env=env, check=True, capture_output=True)
    with psycopg.connect(dsn(name)) as c:
        c.execute("UPDATE companies SET is_active = ticker = ANY(%s)", (TICKERS,))
        dates = [r[0] for r in c.execute("""
            SELECT d::date FROM generate_series(expected_latest_trading_day(now()) - 30,
                                                expected_latest_trading_day(now()), INTERVAL '1 day') d
            WHERE is_trading_day(d::date) ORDER BY d DESC LIMIT 6""")]
        c.commit()
    yield name, ",".join(str(d) for d in sorted(dates))
    with admin() as a:
        a.execute(f'DROP DATABASE IF EXISTS "{name}"')


COUNTER = [0]


@pytest.fixture
def scratch(template):
    import psycopg
    tmpl, dates = template
    COUNTER[0] += 1
    name = f"{tmpl[:-5]}_{COUNTER[0]}"
    with admin() as a:
        a.execute(f'DROP DATABASE IF EXISTS "{name}"')
        a.execute(f'CREATE DATABASE "{name}" TEMPLATE "{tmpl}"')
    conn = psycopg.connect(dsn(name), autocommit=True)
    yield name, dates, conn
    conn.close()
    with admin() as a:
        a.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def worker(name, dates, stage="none", target="", wait="0", ttl="120", extra=None, background=False):
    env = {**os.environ, "DATABASE_URL": dsn(name), "CRASH_DATES": dates, "CRASH_TICKERS": ",".join(TICKERS),
           "PIPELINE_LEASE_WAIT_S": wait, "PIPELINE_LEASE_TTL_S": ttl, "PIPELINE_HEARTBEAT_S": "0.5",
           "PIPELINE_LEASE_POLL_S": "0.2", **(extra or {})}
    cmd = [sys.executable, str(WORKER), stage, target]
    if background:
        return subprocess.Popen(cmd, env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True, text=True, timeout=180)


SNAPSHOT = {
    "daily_prices": "SELECT ticker, price_date, open, high, low, close, adj_close, volume, source FROM daily_prices",
    "price_versions": "SELECT ticker, price_date, close, adj_close, volume, source, sys_to IS NULL FROM price_versions",
    "price_observations": "SELECT ticker, price_date, source, value_hash FROM price_observations",
    "price_selection": """SELECT s.ticker, s.price_date, s.action, s.value_hash, o.value_hash
                          FROM price_selection s LEFT JOIN price_observations o USING (observation_id)""",
    "quality_states": "SELECT ticker, price_date, state, reason, close FROM quality_states",
    "quality_current": "SELECT ticker, price_date, state FROM v_quality_current",
    "signals": """SELECT ticker, signal_date, return_1d, momentum_20d, rsi_14, trend, is_anomaly, high_52w, breakout_52w
                  FROM signals""",
}


def snapshot(conn):
    return {k: sorted(map(repr, conn.execute(q).fetchall())) for k, q in SNAPSHOT.items()}


@pytest.fixture(scope="module")
def clean_run(template):
    import psycopg
    tmpl, dates = template
    name = f"{tmpl[:-5]}_baseline"
    with admin() as a:
        a.execute(f'DROP DATABASE IF EXISTS "{name}"')
        a.execute(f'CREATE DATABASE "{name}" TEMPLATE "{tmpl}"')
    out = worker(name, dates)
    assert out.returncode == 0, out.stdout + out.stderr
    with psycopg.connect(dsn(name)) as c:
        snap = snapshot(c)
    with admin() as a:
        a.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    assert len(snap["daily_prices"]) == 3 * 6
    return snap


def runs(conn):
    return conn.execute("SELECT run_id, job_name, status, message FROM pipeline_runs ORDER BY run_id").fetchall()


def lease(conn):
    return conn.execute("SELECT run_id, is_live FROM v_run_leases WHERE lease_name = 'pipeline'").fetchone()


def tickers_stored(conn):
    return sorted({r[0] for r in conn.execute("SELECT ticker FROM daily_prices")})


def expire_lease(conn):
    conn.execute("UPDATE run_leases SET expires_at = clock_timestamp() - INTERVAL '1 second' WHERE lease_name = 'pipeline'")


def age_run(conn, run_id, hours=2):
    conn.execute(f"""UPDATE pipeline_runs SET started_at = started_at - INTERVAL '{hours} hours',
                     heartbeat_at = heartbeat_at - INTERVAL '{hours} hours' WHERE run_id = %s""", (run_id,))


@pytest.mark.parametrize("stage,target,stored_after_crash,recovery", [
    ("before_writes", "", [], "reaper"),
    ("mid_transaction", "TCS.NS", ["INFY.NS"], "takeover"),
    ("after_commit", "TCS.NS", ["INFY.NS", "TCS.NS"], "reaper"),
])
def test_killed_run_loses_and_duplicates_nothing(scratch, clean_run, stage, target, stored_after_crash, recovery):
    name, dates, conn = scratch
    crashed = worker(name, dates, stage, target)
    assert crashed.returncode == -9, crashed.stdout + crashed.stderr
    (run_id, job, status, _), = runs(conn)
    assert (job, status) == ("daily_update", "running")
    assert lease(conn) == (run_id, True)
    assert tickers_stored(conn) == stored_after_crash
    assert not conn.execute("SELECT COUNT(*) FROM pg_stat_activity WHERE datname = %s AND state LIKE 'idle in transaction%%'", (name,)).fetchone()[0]

    blocked = worker(name, dates)
    assert blocked.returncode != 0 and "RunLeaseBusy" in blocked.stdout + blocked.stderr
    refused = runs(conn)[-1]
    assert refused[2] == "failed" and f"held by run {run_id}" in refused[3]
    assert tickers_stored(conn) == stored_after_crash

    if recovery == "reaper":
        expire_lease(conn)
        age_run(conn, run_id)
        env = {**os.environ, "DATABASE_URL": dsn(name)}
        out = subprocess.run([sys.executable, str(ROOT / "src" / "run_lease.py")], env=env, capture_output=True, text=True)
        assert out.returncode == 0 and "stale runs marked failed: 1" in out.stdout, out.stdout + out.stderr
        assert "stale-run detector" in runs(conn)[0][3]
    else:
        expire_lease(conn)

    rerun = worker(name, dates)
    assert rerun.returncode == 0, rerun.stdout + rerun.stderr
    final = runs(conn)
    assert final[0][2] == "failed"
    expected_note = "stale-run detector" if recovery == "reaper" else "taken over by run"
    assert expected_note in final[0][3]
    assert final[-1][2] == "success"
    assert lease(conn) == (None, False)
    assert snapshot(conn) == clean_run
    assert not conn.execute("SELECT COUNT(*) FROM v_lease_overlaps").fetchone()[0]


def test_two_live_runs_never_overlap(scratch, tmp_path):
    name, dates, conn = scratch
    hold = tmp_path / "go"
    first = worker(name, dates, "hold", extra={"CRASH_HOLD_FILE": str(hold)}, background=True)
    deadline = time.time() + 60
    while not hold.with_suffix(".started").exists():
        assert first.poll() is None, first.stdout.read()
        assert time.time() < deadline
        time.sleep(0.05)
    holder = lease(conn)
    second = worker(name, dates, wait="1")
    assert second.returncode != 0 and "RunLeaseBusy" in second.stdout + second.stderr
    beat_before = conn.execute("SELECT heartbeat_at FROM run_leases WHERE lease_name = 'pipeline'").fetchone()[0]
    time.sleep(1.5)
    beat_after = conn.execute("SELECT heartbeat_at FROM run_leases WHERE lease_name = 'pipeline'").fetchone()[0]
    assert beat_after > beat_before
    waiting = worker(name, dates, wait="60", background=True)
    time.sleep(1.0)
    assert waiting.poll() is None
    hold.write_text("go")
    assert first.wait(120) == 0, first.stdout.read()
    assert waiting.wait(120) == 0, waiting.stdout.read()
    statuses = [r[2] for r in runs(conn)]
    assert statuses == ["success", "failed", "success"] and holder[1]
    assert not conn.execute("SELECT COUNT(*) FROM v_lease_overlaps").fetchone()[0]
    events = [r[0] for r in conn.execute("SELECT event FROM run_lease_log ORDER BY event_id")]
    assert events == ["acquired", "refused", "released", "acquired", "released"]


def test_reaper_leaves_live_and_recent_runs_alone(scratch):
    name, dates, conn = scratch
    live = conn.execute("INSERT INTO pipeline_runs (job_name) VALUES ('live') RETURNING run_id").fetchone()[0]
    conn.execute("SELECT acquire_run_lease('pipeline', %s, 'live', INTERVAL '10 minutes')", (live,))
    age_run(conn, live, 5)
    recent = conn.execute("INSERT INTO pipeline_runs (job_name) VALUES ('recent') RETURNING run_id").fetchone()[0]
    dead = conn.execute("INSERT INTO pipeline_runs (job_name) VALUES ('dead') RETURNING run_id").fetchone()[0]
    conn.execute("UPDATE pipeline_runs SET started_at = now() - INTERVAL '3 hours' WHERE run_id = %s", (dead,))
    reaped = [r[0] for r in conn.execute("SELECT run_id FROM reap_stale_runs(INTERVAL '30 minutes')")]
    assert reaped == [dead]
    status = dict(conn.execute("SELECT run_id, status FROM pipeline_runs").fetchall())
    assert (status[live], status[recent], status[dead]) == ("running", "running", "failed")
    with pytest.raises(Exception):
        conn.execute("DELETE FROM run_lease_log")


def test_a_finished_run_cannot_overwrite_a_failure_recorded_by_the_detector(scratch):
    import psycopg
    import db
    name, dates, conn = scratch
    with psycopg.connect(dsn(name)) as c:
        with db.pipeline_run(c, "slow") as run:
            conn.execute("SELECT * FROM reap_stale_runs(INTERVAL '0 seconds')")
            assert conn.execute("SELECT status FROM pipeline_runs WHERE run_id = %s", (run["run_id"],)).fetchone()[0] == "running"
            conn.execute("UPDATE run_leases SET expires_at = clock_timestamp() - INTERVAL '1 second'")
            conn.execute("SELECT * FROM reap_stale_runs(INTERVAL '0 seconds')")
            with pytest.raises(run_lease.LeaseLost):
                run_lease.fence(c)
            c.rollback()
            run_id = run["run_id"]
    assert conn.execute("SELECT status FROM pipeline_runs WHERE run_id = %s", (run_id,)).fetchone()[0] == "failed"

"""Shared database helpers: connection + pipeline run logging."""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg

import run_lease

try:  # load .env when running locally; GitHub Actions passes env vars directly
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def get_conn() -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit(
            "DATABASE_URL is not set. Copy .env.example to .env and paste your Neon connection string."
        )
    return psycopg.connect(url, connect_timeout=30)


def active_tickers(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM companies WHERE is_active ORDER BY ticker")
        return [r[0] for r in cur.fetchall()]


_CURRENT_RUN = {"id": None}


def current_run_id():
    """run_id of the pipeline_run() block in progress (None outside one)."""
    return _CURRENT_RUN["id"]


def record_settings(conn, run_id, settings_id) -> None:
    if run_id is None or settings_id is None:
        return
    with conn.cursor() as cur:
        cur.execute("UPDATE pipeline_runs SET settings_id = %s WHERE run_id = %s", (settings_id, run_id))
    conn.commit()


def _take_lease(conn, run_id, job_name):
    if not run_lease.available(conn):
        return None
    run_lease.reap(conn, run_id)
    holding = run_lease.acquire(conn, run_id, job_name)
    run_lease.HELD["holding"] = holding
    return run_lease.start_heartbeat(holding, get_conn)


def _drop_lease(conn, holding):
    if holding is None:
        return
    run_lease.stop_heartbeat(holding)
    run_lease.HELD["holding"] = None
    try:
        run_lease.release(conn, holding)
    except Exception:
        conn.rollback()


@contextmanager
def pipeline_run(conn, job_name: str):
    """Logs a row in pipeline_runs. Yields a dict the job fills with
    rows_upserted / tickers_failed / message."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pipeline_runs (job_name) VALUES (%s) RETURNING run_id", (job_name,)
        )
        run_id = cur.fetchone()[0]
    _CURRENT_RUN["id"] = run_id
    conn.commit()
    info = {"rows_upserted": 0, "tickers_failed": [], "message": None, "run_id": run_id}
    status = "failed"
    holding = None
    try:
        holding = _take_lease(conn, run_id, job_name)
        yield info
        status = "partial" if info["tickers_failed"] else "success"
    except BaseException as exc:
        conn.rollback()
        info["message"] = f"{type(exc).__name__}: {exc}"[:2000]
        raise
    finally:
        try:
            if holding is not None:
                run_lease.stop_heartbeat(holding)
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE pipeline_runs
                       SET finished_at = now(), status = %s, rows_upserted = %s,
                           tickers_failed = %s, message = %s
                       WHERE run_id = %s AND status = 'running'""",
                    (status, info["rows_upserted"], info["tickers_failed"], info["message"], run_id),
                )
            conn.commit()
        finally:
            _drop_lease(conn, holding)
            _CURRENT_RUN["id"] = None

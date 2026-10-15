"""Shared database helpers: connection + pipeline run logging."""
import os
from contextlib import contextmanager
from pathlib import Path

import psycopg

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


@contextmanager
def pipeline_run(conn, job_name: str):
    """Logs a row in pipeline_runs. Yields a dict the job fills with
    rows_upserted / tickers_failed / message."""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO pipeline_runs (job_name) VALUES (%s) RETURNING run_id", (job_name,)
        )
        run_id = cur.fetchone()[0]
    # provenance: prices.upsert_rows() passes this id to the versioning trigger
    # (sql/08_point_in_time.sql) inside each write transaction
    _CURRENT_RUN["id"] = run_id
    conn.commit()
    info = {"rows_upserted": 0, "tickers_failed": [], "message": None, "run_id": run_id}
    try:
        yield info
        status = "partial" if info["tickers_failed"] else "success"
    except Exception as exc:
        conn.rollback()
        info["message"] = f"{type(exc).__name__}: {exc}"[:2000]
        status = "failed"
        raise
    finally:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE pipeline_runs
                   SET finished_at = now(), status = %s, rows_upserted = %s,
                       tickers_failed = %s, message = %s
                   WHERE run_id = %s""",
                (status, info["rows_upserted"], info["tickers_failed"], info["message"], run_id),
            )
        conn.commit()
        _CURRENT_RUN["id"] = None

"""Apply SQL files in sql/ that have not been applied yet.
Usage:  python src/setup_db.py      (safe to run many times)"""
import hashlib
from pathlib import Path

from db import get_conn
from timing import stage

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class ChecksumMismatch(SystemExit):
    pass


def checksum(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pending(files, applied: dict) -> list:
    changed = [f.name for f in files if f.name in applied and applied[f.name] != checksum(f.read_text())]
    if changed:
        raise ChecksumMismatch(
            "Applied migration(s) changed on disk: " + ", ".join(changed)
            + ". Put the change in a new SQL file instead of editing an applied one."
        )
    return [f for f in files if f.name not in applied]


def apply(conn, files) -> list:
    conn.execute(CREATE_SQL)
    applied = dict(conn.execute("SELECT filename, checksum FROM schema_migrations").fetchall())
    todo = pending(files, applied)
    for f in todo:
        text = f.read_text()
        with stage(f"setup_db {f.name}"):
            conn.execute(text)
            conn.execute(
                "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                (f.name, checksum(text)),
            )
    conn.commit()
    return todo


def main():
    files = sorted(SQL_DIR.glob("*.sql"))
    with get_conn() as conn:
        with stage("setup_db total"):
            todo = apply(conn, files)
        print(f"Applied {len(todo)} new SQL file(s); {len(files) - len(todo)} already applied.")
        n = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    print(f"Done. {n} companies in the database.")


if __name__ == "__main__":
    main()

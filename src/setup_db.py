"""Create / update all tables, seed data, signal function and views.
Usage:  python src/setup_db.py      (safe to run many times)"""
from pathlib import Path

from db import get_conn

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def main():
    files = sorted(SQL_DIR.glob("*.sql"))
    with get_conn() as conn:
        for f in files:
            print(f"Running {f.name} ...")
            conn.execute(f.read_text())
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    print(f"Done. {n} companies in the database.")


if __name__ == "__main__":
    main()

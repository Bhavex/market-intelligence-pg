import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql

from scripts.load_sample_data import assert_throwaway


def run(*args):
    print("+", " ".join(args), flush=True)
    subprocess.run([sys.executable, *args], check=True, cwd=ROOT)


def main():
    url = os.environ["DATABASE_URL"]
    assert_throwaway(url)
    run("src/setup_db.py")
    run("scripts/load_sample_data.py", "--years", os.environ.get("SAMPLE_YEARS", "10"))
    reader, password = os.environ.get("READER_USER", "dashboard_reader"), os.environ.get("READER_PASSWORD")
    if password:
        with psycopg.connect(url, autocommit=True) as conn:
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (reader,)).fetchone()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(sql.SQL(verb + " ROLE {} WITH LOGIN PASSWORD {}").format(sql.Identifier(reader), sql.Literal(password)))
            conn.execute(sql.SQL("GRANT readonly_access TO {}").format(sql.Identifier(reader)))
        print(f"read-only login {reader} ready", flush=True)


if __name__ == "__main__":
    main()

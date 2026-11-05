import os

import pytest

import setup_db


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_pending_returns_only_unapplied_files(tmp_path):
    a = write(tmp_path, "01_a.sql", "SELECT 1;")
    b = write(tmp_path, "02_b.sql", "SELECT 2;")
    applied = {"01_a.sql": setup_db.checksum("SELECT 1;")}
    assert setup_db.pending([a, b], applied) == [b]


def test_pending_fails_loudly_when_applied_file_changes(tmp_path):
    a = write(tmp_path, "01_a.sql", "SELECT 1; SELECT 3;")
    applied = {"01_a.sql": setup_db.checksum("SELECT 1;")}
    with pytest.raises(setup_db.ChecksumMismatch, match="01_a.sql"):
        setup_db.pending([a], applied)


def test_checksum_is_sha256_hex():
    assert len(setup_db.checksum("x")) == 64
    assert setup_db.checksum("x") != setup_db.checksum("y")


TEST_DB = os.environ.get("TEST_DATABASE_URL")


@pytest.mark.skipif(not TEST_DB, reason="set TEST_DATABASE_URL to run database tests")
def test_apply_records_and_skips(tmp_path):
    import psycopg
    conn = psycopg.connect(TEST_DB)
    try:
        conn.execute("DROP TABLE IF EXISTS schema_migrations_probe")
        conn.execute(setup_db.CREATE_SQL)
        conn.execute("DELETE FROM schema_migrations WHERE filename LIKE 'zz_probe%%'")
        conn.commit()
        f = write(tmp_path, "zz_probe_01.sql", "CREATE TABLE schema_migrations_probe (n int); INSERT INTO schema_migrations_probe VALUES (1);")
        assert setup_db.apply(conn, [f]) == [f]
        assert setup_db.apply(conn, [f]) == []
        assert conn.execute("SELECT COUNT(*) FROM schema_migrations_probe").fetchone()[0] == 1
        f.write_text(f.read_text() + " SELECT 1;")
        with pytest.raises(setup_db.ChecksumMismatch):
            setup_db.apply(conn, [f])
        conn.rollback()
    finally:
        conn.rollback()
        conn.execute("DROP TABLE IF EXISTS schema_migrations_probe")
        conn.execute("DELETE FROM schema_migrations WHERE filename LIKE 'zz_probe%%'")
        conn.commit()
        conn.close()

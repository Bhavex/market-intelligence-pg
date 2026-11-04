import json
import logging

import fetch_config
import quality
import validation

log = logging.getLogger("market.ledger")

INSERT_SQL = """
INSERT INTO fetch_log (run_id, job_name, source, endpoint, symbol, request, retrieved_at, response_sha256, hash_basis,
                       response_bytes, rows_returned, rows_parsed, rows_valid, rows_rejected, rows_suspect, rows_held_back,
                       library, library_version, settings_id, parser_version, validation_rule_version,
                       quality_rule_version, status, error)
VALUES (%(run_id)s, %(job_name)s, %(source)s, %(endpoint)s, %(symbol)s, %(request)s::jsonb, %(retrieved_at)s,
        %(response_sha256)s, %(hash_basis)s, %(response_bytes)s, %(rows_returned)s, %(rows_parsed)s, %(rows_valid)s,
        %(rows_rejected)s, %(rows_suspect)s, %(rows_held_back)s, %(library)s, %(library_version)s, %(settings_id)s,
        %(parser_version)s, %(validation_rule_version)s, %(quality_rule_version)s, %(status)s, %(error)s)
RETURNING fetch_id
"""


def _exists(conn) -> bool:
    return conn.execute("SELECT to_regclass('public.fetch_log')").fetchone()[0] is not None


def write(conn, entries: list) -> list:
    if not entries:
        return []
    try:
        with conn.transaction():
            if not _exists(conn):
                return []
            ids = []
            for e in entries:
                row = conn.execute(INSERT_SQL, {**e, "request": json.dumps(e.get("request", {}), default=str)}).fetchone()
                ids.append(row[0] if row else None)
            return ids
    except Exception as exc:
        log.warning("could not write %d fetch ledger row(s): %s", len(entries), exc)
        return []


def yahoo_entry(rec, run_id=None, job_name=None, settings_id=None, result=None, held_back=None) -> dict:
    return {
        "run_id": run_id, "job_name": job_name, "source": fetch_config.SOURCE, "endpoint": f"history:{rec.interval}",
        "symbol": rec.symbol, "request": {**rec.params, **fetch_config.history_kwargs(rec.interval)},
        "retrieved_at": rec.retrieved_at, "response_sha256": rec.response_sha256, "hash_basis": "canonical_frame_csv",
        "response_bytes": None, "rows_returned": rec.rows_returned, "rows_parsed": rec.rows_parsed,
        "rows_valid": None if result is None else len(result.valid),
        "rows_rejected": None if result is None else len(result.rejected),
        "rows_suspect": None if result is None else len(result.suspect),
        "rows_held_back": held_back, "library": fetch_config.LIBRARY, "library_version": fetch_config.library_version(),
        "settings_id": settings_id, "parser_version": _parser_version(),
        "validation_rule_version": validation.RULE_VERSION, "quality_rule_version": quality.RULE_VERSION,
        "status": rec.status, "error": rec.error,
    }


def _parser_version() -> str:
    import prices
    return prices.PARSER_VERSION


def record_fetch(conn, fetched, result, run_id=None, job_name=None, settings_daily=None, settings_hourly=None) -> list:
    entries = []
    for i, rec in enumerate(fetched.requests):
        daily = rec.interval == "1d"
        entries.append(yahoo_entry(rec, run_id, job_name, settings_daily if daily else settings_hourly,
                                   result if i == 0 else None, fetched.held_back if daily else None))
    return write(conn, entries)


def record_error(conn, rec, run_id=None, job_name=None, settings_id=None) -> list:
    return write(conn, [yahoo_entry(rec, run_id, job_name, settings_id)])

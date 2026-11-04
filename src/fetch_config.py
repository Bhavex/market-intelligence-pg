import hashlib
import json
import logging
from importlib.metadata import PackageNotFoundError, version

log = logging.getLogger("market.fetch_config")

PINNED_YFINANCE = "1.8.0"
SOURCE = "yahoo"
LIBRARY = "yfinance"

HISTORY_FLAGS = {
    "auto_adjust": False,
    "back_adjust": False,
    "repair": False,
    "actions": False,
    "prepost": False,
    "keepna": False,
    "rounding": False,
    "raise_errors": False,
    "timeout": 30,
}

CLOSE_BASIS = "split_adjusted"
ADJ_CLOSE_BASIS = "split_and_dividend_adjusted"
OFFICIAL_CLOSE_BASIS = "unadjusted"


def library_version() -> str:
    try:
        return version(LIBRARY)
    except PackageNotFoundError:
        return "not-installed"


def history_kwargs(interval: str) -> dict:
    return {**HISTORY_FLAGS, "interval": interval}


def settings(interval: str = "1d") -> dict:
    return {
        "source": SOURCE,
        "library": LIBRARY,
        "library_version": library_version(),
        "pinned_version": PINNED_YFINANCE,
        "interval": interval,
        "params": dict(sorted(HISTORY_FLAGS.items())),
        "close_basis": CLOSE_BASIS,
        "adj_close_basis": ADJ_CLOSE_BASIS,
    }


def settings_hash(s: dict) -> str:
    return hashlib.sha256(json.dumps(s, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_version() -> bool:
    installed = library_version()
    if installed != PINNED_YFINANCE:
        log.warning("yfinance %s is installed but %s is pinned; the load records the installed version",
                    installed, PINNED_YFINANCE)
        return False
    return True


REGISTER_SQL = """
INSERT INTO fetch_settings (settings_hash, source, library, library_version, interval, params, close_basis, adj_close_basis)
VALUES (%(hash)s, %(source)s, %(library)s, %(library_version)s, %(interval)s, %(params)s::jsonb, %(close_basis)s, %(adj_close_basis)s)
ON CONFLICT (settings_hash) DO UPDATE SET last_used_at = now()
RETURNING settings_id
"""


def register(conn, interval: str = "1d"):
    s = settings(interval)
    try:
        with conn.transaction():
            row = conn.execute(REGISTER_SQL, {**s, "hash": settings_hash(s), "params": json.dumps(s["params"])}).fetchone()
        return row[0] if row else None
    except Exception as exc:
        log.warning("could not record fetch settings: %s", exc)
        return None

from contextlib import contextmanager
from importlib.metadata import PackageNotFoundError

import fetch_config


class Res:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class Conn:
    def __init__(self, row=(1,), fail=False):
        self.row = row
        self.fail = fail
        self.params = None

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=None):
        if self.fail:
            raise RuntimeError("db down")
        self.params = params
        return Res(self.row)


def test_library_version_not_installed(monkeypatch):
    def missing(name):
        raise PackageNotFoundError(name)
    monkeypatch.setattr(fetch_config, "version", missing)
    assert fetch_config.library_version() == "not-installed"


def test_check_version_mismatch_and_match(monkeypatch):
    monkeypatch.setattr(fetch_config, "library_version", lambda: "0.0.1")
    assert fetch_config.check_version() is False
    monkeypatch.setattr(fetch_config, "library_version", lambda: fetch_config.PINNED_YFINANCE)
    assert fetch_config.check_version() is True


def test_history_kwargs_does_not_mutate_flags():
    kw = fetch_config.history_kwargs("1h")
    kw["timeout"] = 1
    assert fetch_config.HISTORY_FLAGS["timeout"] == 30
    assert "interval" not in fetch_config.HISTORY_FLAGS


def test_settings_hash_is_order_independent_and_interval_sensitive(monkeypatch):
    monkeypatch.setattr(fetch_config, "library_version", lambda: "1.8.0")
    a = fetch_config.settings("1d")
    b = dict(reversed(list(a.items())))
    assert fetch_config.settings_hash(a) == fetch_config.settings_hash(b)
    assert fetch_config.settings_hash(a) != fetch_config.settings_hash(fetch_config.settings("1h"))


def test_register_returns_id_and_serialises_params(monkeypatch):
    monkeypatch.setattr(fetch_config, "library_version", lambda: "1.8.0")
    c = Conn(row=(42,))
    assert fetch_config.register(c, "1h") == 42
    assert c.params["interval"] == "1h"
    assert isinstance(c.params["params"], str)
    assert len(c.params["hash"]) == 64


def test_register_no_row_returns_none():
    assert fetch_config.register(Conn(row=None)) is None


def test_register_failure_returns_none():
    assert fetch_config.register(Conn(fail=True)) is None

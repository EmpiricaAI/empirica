"""database_config silently ignored three things a user explicitly set (sweep item R8, goal b78396ad; reproduced at HEAD 2026-10-25).

* `DATABASE_URL=postgres://...` (the Heroku/Docker spelling its own docstring promises) was ignored: only `postgresql` prefixed URLs matched.
* A `${VAR}` in config.yaml that was not set stayed the literal string `${VAR}` with no word, so a password became "${NOPE_PW}".
* A config.yaml that did not parse fell back to SQLite at DEBUG level: a user who configured PostgreSQL got SQLite and no signal.
Nothing here crashes on a bad config: this runs on every database open, so the fix is that the fallback says so.
"""

from __future__ import annotations

import logging

import pytest

from empirica.config import database_config as dc


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ("DATABASE_URL", "EMPIRICA_DB_TYPE", "NOPE_PW", "SET_PW"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("scheme", ["postgres", "postgresql"])
def test_both_spellings_of_a_postgres_url_select_postgresql(monkeypatch, scheme):
    monkeypatch.setenv("DATABASE_URL", f"{scheme}://u:p@h:5433/db")
    cfg = dc.get_database_config()
    assert cfg["type"] == "postgresql"
    assert cfg["postgresql"] == {"host": "h", "port": 5433, "database": "db", "user": "u", "password": "p"}


def test_a_url_for_some_other_database_is_not_taken_for_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@h/db")
    assert dc.get_database_config()["type"] == "sqlite"


def _config_in(tmp_path, monkeypatch, text):
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "config.yaml").write_text(text)
    monkeypatch.setattr("empirica.config.path_resolver.get_git_root", lambda: tmp_path)


def test_an_unset_variable_in_config_yaml_is_named_in_a_warning(tmp_path, monkeypatch, caplog):
    _config_in(tmp_path, monkeypatch, "database:\n  type: postgresql\n  postgresql:\n    password: ${NOPE_PW}\n")
    with caplog.at_level(logging.WARNING, logger=dc.logger.name):
        dc.get_database_config()
    assert any("NOPE_PW" in r.getMessage() and r.levelno >= logging.WARNING for r in caplog.records)


def test_a_variable_that_is_set_is_substituted_without_a_warning(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("SET_PW", "s3cret")
    _config_in(tmp_path, monkeypatch, "database:\n  type: postgresql\n  postgresql:\n    password: ${SET_PW}\n")
    with caplog.at_level(logging.WARNING, logger=dc.logger.name):
        cfg = dc.get_database_config()
    assert cfg["postgresql"]["password"] == "s3cret"  # noqa: S105 - a fixture value, set two lines up
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_a_config_yaml_that_does_not_parse_says_so_and_says_it_fell_back(tmp_path, monkeypatch, caplog):
    _config_in(tmp_path, monkeypatch, "database: [oops\n")
    with caplog.at_level(logging.WARNING, logger=dc.logger.name):
        cfg = dc.get_database_config()
    assert cfg["type"] == "sqlite", "a bad config does not crash the database open"
    messages = " ".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
    assert "config.yaml" in messages and "SQLite" in messages

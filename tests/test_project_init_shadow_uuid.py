"""project-init must not leave a shadow UUID: an explicit / reused project_id
should materialize the sessions.db projects row under THAT id.

Regression guard for the epistemic-dj repair bug (part 2): create_project ignored
any caller id and always minted a fresh UUID, so a --project-id link / repair left
sessions.db without a row under the id project.yaml claimed.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.project_init import _ensure_project_row
from empirica.data.session_database import SessionDatabase


@pytest.fixture
def db(tmp_path):
    """Was a plain _db() helper calling mkdtemp() with no teardown at all.

    A fixture rather than a helper because the cleanup has to be owned by
    something — a bare function returning a DB has nowhere to put it.
    """
    return SessionDatabase(db_path=str(tmp_path / "s.db"))


def test_create_project_honors_explicit_id(db):
    pid = db.create_project(name="X", project_id="my-fixed-id")
    assert pid == "my-fixed-id"
    assert db.get_project("my-fixed-id") is not None


def test_create_project_auto_generates_without_id(db):
    pid = db.create_project(name="Y")
    assert pid and pid != "Y"
    assert db.get_project(pid) is not None


def test_ensure_project_row_materializes_missing_id(db):
    assert db.get_project("ghost-id") is None
    _ensure_project_row(db, "ghost-id", "Name", "desc", None, "product", [], "json")
    assert db.get_project("ghost-id") is not None


def test_ensure_project_row_noop_when_present(db):
    db.create_project(name="Z", project_id="present-id")
    _ensure_project_row(db, "present-id", "Z", "desc", None, "product", [], "json")  # must not raise/dupe
    assert db.get_project("present-id") is not None

"""Deleting a project: the project store owns it, `entity-delete` routes to it.

Removing a practice left its `global_projects` row behind — the row had a writer
(`upsert_project`) and no deleter, so cleanup meant deleting by id. Reported by
ecodex during EXP-SHADOW arm teardown.

The seam matters more than the gap. `entity_registry` is an INDEX;
`global_projects` is a record-of-truth with its own writer. An index verb that
mutates a detail table has made itself an owner, which is the boundary violation
rather than the fix. So the delete lives beside the write, and `entity-delete`
delegates: **whoever owns the write owns the delete.**

And the reference check spans Qdrant, not just SQL. Deleting a project row while
leaving its collections is exactly how 13 orphaned collections holding 264 points
were minted on this box — a check that ignores Qdrant manufactures that residue
deliberately and reports success.
"""

from __future__ import annotations

import pytest

from empirica.data.repositories.workspace_db import WorkspaceDBRepository


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(tmp_path / "workspace.db"))
    monkeypatch.setenv("HOME", str(tmp_path))
    with WorkspaceDBRepository.open(ensure_schema=True) as r:
        r.upsert_project(
            project_id="p-doomed",
            name="doomed",
            trajectory_path=str(tmp_path / "doomed" / ".empirica"),
        )
        yield r


def _no_qdrant(monkeypatch):
    """Qdrant reachable and holding nothing for this project."""
    import urllib.request

    class _R:
        def read(self):
            return b'{"result":{"collections":[]}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _R())


def test_the_delete_lives_where_the_write_lives(repo):
    """The seam, asserted as an API fact: the project store has a deleter."""
    assert hasattr(repo, "delete_project")
    assert hasattr(repo, "upsert_project")


def test_a_clean_project_deletes(repo, monkeypatch):
    _no_qdrant(monkeypatch)

    result = repo.delete_project("p-doomed")

    assert result["ok"] and result["deleted"]
    assert repo.get_project_by_id("p-doomed") is None


def test_deleting_an_absent_project_is_NOT_reported_as_a_cleanup(repo, monkeypatch):
    """Absent is not deleted. Reporting a no-op as a cleanup is the class this
    repo has removed repeatedly."""
    _no_qdrant(monkeypatch)

    result = repo.delete_project("p-never-existed")

    assert result["ok"] is True
    assert result["deleted"] is False
    assert "no such project" in result["reason"]


def test_an_UNREACHABLE_qdrant_refuses_rather_than_assuming_zero(repo, monkeypatch):
    """THE control that matters. An unreachable backend returning no collections
    is not the same as a backend saying there are none — and deleting on the
    first is precisely how orphaned collections get minted."""
    import urllib.request

    def _boom(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)

    result = repo.delete_project("p-doomed")

    assert result["ok"] is False
    assert result["deleted"] is False
    assert "unknown is not zero" in result["error"]
    assert repo.get_project_by_id("p-doomed") is not None


def test_remaining_qdrant_collections_BLOCK_and_are_named(repo, monkeypatch):
    import urllib.request

    class _R:
        def read(self):
            return b'{"result":{"collections":[{"name":"project_p-doomed_eidetic"},{"name":"project_other_memory"}]}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _R())

    result = repo.delete_project("p-doomed")

    assert result["ok"] is False
    assert result["references"]["qdrant_collections"] == ["project_p-doomed_eidetic"]
    # and NOT another project's collection — the prefix must be exact
    assert "project_other_memory" not in result["references"]["qdrant_collections"]


def test_force_proceeds_but_still_REPORTS_what_was_overridden(repo, monkeypatch):
    """An operator who overrides a refusal should see exactly what they
    overrode — otherwise force silently converts a considered refusal into an
    unrecorded one."""
    import urllib.request

    class _R:
        def read(self):
            return b'{"result":{"collections":[{"name":"project_p-doomed_eidetic"}]}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _R())

    result = repo.delete_project("p-doomed", force=True)

    assert result["deleted"] is True
    assert result["forced"] is True
    assert result["references"]["qdrant_collections"] == ["project_p-doomed_eidetic"]


def test_entity_delete_ROUTES_rather_than_reaching():
    """Structural. `entity-delete` must delegate to the project store, never
    execute SQL against global_projects itself — an index verb that mutates a
    detail table has made itself an owner."""
    import ast
    import inspect

    import empirica.cli.command_handlers.entity_commands as mod

    src = inspect.getsource(mod)
    assert "delete_project(" in src, "entity-delete must delegate to the project store"

    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            sql = node.value.upper()
            if "DELETE FROM" in sql and "GLOBAL_PROJECTS" in sql:
                raise AssertionError(
                    f"line {node.lineno}: entity_commands executes SQL against global_projects — "
                    "it must ROUTE to the project store, not REACH into its table"
                )

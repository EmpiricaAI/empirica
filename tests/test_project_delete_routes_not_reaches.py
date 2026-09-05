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


def test_an_UNREADABLE_SQL_TABLE_refuses_too_not_only_qdrant(repo, monkeypatch):
    """The asymmetry that shipped: `unknown is not zero` held for ONE backend.

    `project_references` catches a missing `entity_registry` (older schema) and
    records the string "unavailable: OperationalError". The first refusal filter
    scored anything that was not an int-or-list as 0, so that string read as "no
    rows point here" and the delete PROCEEDED — while the Qdrant half of the same
    function correctly refused on None.

    A safety principle applied to the backend whose failure mode was in mind, and
    not to the one that wasn't. Asserted per-backend rather than once, because a
    single case would have passed against the broken version.
    """
    _no_qdrant(monkeypatch)
    monkeypatch.setattr(
        type(repo),
        "project_references",
        lambda self, pid: {
            "entity_registry": "unavailable: OperationalError",
            "entity_memberships": 0,
            "qdrant_collections": [],
        },
    )

    result = repo.delete_project("p-doomed")

    assert result["ok"] is False, "an unreadable reference table must refuse, exactly as an unreachable Qdrant does"
    assert result["deleted"] is False
    assert "entity_registry" in result["unchecked"]
    assert "unknown is not zero" in result["error"]
    assert repo.get_project_by_id("p-doomed") is not None


def test_the_refusal_names_BOTH_causes_when_both_apply(repo, monkeypatch):
    """An if/else reported only the first cause, so resolving it hit the same
    refusal again with no new information about why."""
    _no_qdrant(monkeypatch)
    monkeypatch.setattr(
        type(repo),
        "project_references",
        lambda self, pid: {
            "entity_registry": 2,
            "entity_memberships": "unavailable: OperationalError",
            "qdrant_collections": [],
        },
    )

    err = repo.delete_project("p-doomed")["error"]

    assert "entity_registry" in err, "the existing references must be named"
    assert "entity_memberships" in err, "the UNCHECKED reference must be named in the same message"


def test_a_clean_check_still_deletes(repo, monkeypatch):
    """Positive control for the two refusals above.

    Both assert that a delete is REFUSED. Against a delete_project that refused
    unconditionally they would both pass, and the verb would be dead. Show the
    same call succeeding on clean input before trusting either refusal.
    """
    _no_qdrant(monkeypatch)
    monkeypatch.setattr(
        type(repo),
        "project_references",
        lambda self, pid: {"entity_registry": 0, "entity_memberships": 0, "qdrant_collections": []},
    )

    result = repo.delete_project("p-doomed")

    assert result["ok"] is True and result["deleted"] is True
    assert repo.get_project_by_id("p-doomed") is None


def test_the_force_escape_the_refusal_ADVERTISES_is_reachable_from_the_CLI():
    """A deny whose stated remedy cannot be taken reads as operator error.

    `delete_project` refuses and says "pass force to proceed"; the handler reads
    `getattr(args, "force", False)`. Nothing added `--force` to the parser, so it
    was permanently False and the advertised escape did not exist on the surface
    the message was written for.

    Asserted against the built parser rather than by grepping the source, because
    the question is what the CLI ACCEPTS, not what a file mentions.
    """
    import argparse

    from empirica.cli.parsers.checkpoint_parsers import add_checkpoint_parsers

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_checkpoint_parsers(subparsers)

    args = parser.parse_args(["entity-delete", "project:p1", "--hard", "--confirm", "--force"])
    assert args.force is True, "--force must reach the handler, not fall through to the getattr default"

    # Positive control: the flag defaults off, so its presence is a real signal
    # rather than a parser that accepts anything.
    assert parser.parse_args(["entity-delete", "project:p1", "--hard", "--confirm"]).force is False

"""`superseded_by` holds the full id of one finding, or the resolution is refused.

Every list view prints ids as eight characters, so that is what a practitioner
has in hand. The column stored it verbatim: two of a peer's four dangling
pointers on 2026-09-21 were the tool's own abbreviated output, pasted back in.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.resolution_kind import UnresolvableFindingRef, canonical_finding_id

FULL = "88f539b3-1111-4222-8333-444455556666"
TWIN_A = "e90c8237-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TWIN_B = "e90c8237-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE project_findings (id TEXT)")
    c.executemany("INSERT INTO project_findings VALUES (?)", [(FULL,), (TWIN_A,), (TWIN_B,)])
    return c


def test_an_eight_character_prefix_is_expanded(conn):
    assert canonical_finding_id(conn.execute, "88f539b3") == FULL


def test_positive_control_a_full_id_passes_through(conn):
    assert canonical_finding_id(conn.execute, FULL) == FULL


def test_empty_stays_none(conn):
    assert canonical_finding_id(conn.execute, None) is None
    assert canonical_finding_id(conn.execute, "  ") is None


@pytest.mark.parametrize(
    ("value", "says"),
    [
        ("c7affb47-142a-481e-b243-d0a17541acf6", "matches no finding"),
        ("deadbeef", "matches no finding"),
        ("e90c8237", "more than one"),
        ("88f5", "too short"),
        ("%%%%%%%%", "matches no finding"),
    ],
)
def test_anything_else_is_refused(conn, value, says):
    with pytest.raises(UnresolvableFindingRef, match=says):
        canonical_finding_id(conn.execute, value)


def test_every_writer_of_the_column_goes_through_it():
    import inspect

    from empirica.api.routes import artifacts
    from empirica.cli.command_handlers import artifact_log_commands, graph_commands
    from empirica.data.repositories import breadcrumbs

    assert "canonical_finding_id(self._execute, superseded_by)" in inspect.getsource(
        breadcrumbs.BreadcrumbRepository.resolve_finding
    )
    assert "canonical_finding_id(db.conn.execute, superseded_by)" in inspect.getsource(
        artifact_log_commands.handle_finding_resolve_command
    )
    assert 'canonical_finding_id(db.conn.execute, item.get("superseded_by"))' in inspect.getsource(graph_commands)
    assert 'canonical_finding_id(cursor.execute, body.get("superseded_by"))' in inspect.getsource(artifacts)

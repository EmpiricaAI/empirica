"""One project, one row — `trajectory_path` has one spelling.

`global_projects.trajectory_path` is declared `TEXT NOT NULL UNIQUE`, and two callers
walked straight past that by spelling the same directory two ways: one passed
`str(project_path / ".empirica")`, the other `str(git_root)`. SQLite compares strings,
so a project registered by both routes produced TWO rows and the constraint never
fired.

**The contract was already written down** — in `_register_in_workspace_db`'s own
docstring, *"trajectory_path: Path to project's .empirica directory."* A docstring
stating a contract is not enforcement, which is how both forms coexisted for as long
as both callers have.

Found from the outside by a peer auditing an unrelated orphan report: one project with
two rows differing only by a trailing `/.empirica`, with the warning that any dedup
keyed on the raw column would treat them as distinct forever. True — and a symptom.
The fix belongs at the WRITE side so no dedup has to know about the spelling.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from empirica.cli.command_handlers.workspace_init import (
    _register_in_workspace_db,
    canonical_trajectory_path,
)


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """`_register_in_workspace_db` writes to `~/.empirica/workspace/workspace.db`.

    Without this the tests would write into the developer's real workspace — green on
    a laptop, and destructive there rather than merely wrong.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def _rows(home: Path) -> list[tuple]:
    db = home / ".empirica" / "workspace" / "workspace.db"
    with sqlite3.connect(str(db)) as conn:
        return conn.execute("SELECT id, trajectory_path FROM global_projects").fetchall()


# ── the normaliser ───────────────────────────────────────────────────────────


def test_both_spellings_normalise_to_the_same_string():
    """THE regression, at the unit. These two are what the two callers passed."""
    root = "/home/u/proj"

    assert canonical_trajectory_path(root) == canonical_trajectory_path(root + "/.empirica")


def test_the_canonical_form_is_the_one_readers_expect():
    """Readers derive the folder name with `Path(...).parent.name` — that only works
    on the `.empirica` form. Canonicalising to the project root instead would have
    made every folder-name lookup return the parent DIRECTORY's name."""
    canonical = canonical_trajectory_path("/home/u/proj")

    assert canonical.endswith("/.empirica")
    assert Path(canonical).parent.name == "proj"


def test_it_is_idempotent():
    """POSITIVE CONTROL. Three of the four callers were already correct; a normaliser
    that appended unconditionally would have broken them into `.empirica/.empirica`."""
    once = canonical_trajectory_path("/home/u/proj")

    assert canonical_trajectory_path(once) == once


# ── the property that actually matters ───────────────────────────────────────


def test_registering_the_same_project_both_ways_yields_ONE_row(isolated_home, tmp_path):
    """THE defect, at the level a peer observed it: one project, two rows.

    Deliberately registers by the two forms the two real callers use, in that order,
    with the SAME project id — which is the real-world sequence (a project inited one
    way and later re-registered another).
    """
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    pid = "11111111-1111-1111-1111-111111111111"

    assert _register_in_workspace_db(project_id=pid, name="proj", trajectory_path=str(proj / ".empirica"))
    assert _register_in_workspace_db(project_id=pid, name="proj", trajectory_path=str(proj))

    rows = _rows(isolated_home)
    assert len(rows) == 1, f"one project produced {len(rows)} rows: {rows}"
    assert rows[0][1].endswith("/.empirica")


def test_the_bare_root_form_alone_still_stores_canonically(isolated_home, tmp_path):
    """The divergent caller on its own must not write the non-canonical form either —
    otherwise the row is a landmine for the next writer rather than a duplicate now."""
    proj = tmp_path / "solo"
    (proj / ".empirica").mkdir(parents=True)

    _register_in_workspace_db(project_id="22222222-2222-2222-2222-222222222222", name="solo", trajectory_path=str(proj))

    assert _rows(isolated_home)[0][1] == str(proj / ".empirica")


def test_two_genuinely_different_projects_still_get_two_rows(isolated_home, tmp_path):
    """NEGATIVE CONTROL, and the failure mode that would be worse than the bug.

    A normaliser that collapsed distinct projects would silently merge two practices'
    registrations — losing one entirely rather than duplicating it.
    """
    for i, name in enumerate(("alpha", "beta")):
        p = tmp_path / name
        (p / ".empirica").mkdir(parents=True)
        _register_in_workspace_db(
            project_id=f"{i}" * 8 + "-0000-0000-0000-000000000000", name=name, trajectory_path=str(p)
        )

    assert len(_rows(isolated_home)) == 2


def test_every_writer_of_global_projects_normalises():
    """THE class, and it caught a second writer on its first run.

    A normaliser only helps callers that route through it. I fixed the CLI helper and
    assumed that was the write path; this test found
    `data/repositories/workspace_db.py` writing `global_projects` directly. That one
    dedupes on `ON CONFLICT(id)` so it cannot duplicate a row for one project — but it
    could store a non-canonical spelling, which makes the row a landmine for the other
    writer, whose lookup is a string equality on this column.

    Asserted structurally rather than by a fixed allowlist: any file that INSERTs into
    the table must also reference the normaliser, so a third writer added later fails
    here instead of silently reintroducing the divergence.
    """
    import re

    root = Path(__file__).parent.parent / "empirica"
    unnormalised = []
    for path in root.rglob("*.py"):
        src = path.read_text()
        if not re.search(r"INSERT\s+(OR\s+\w+\s+)?INTO\s+global_projects", src, re.I):
            continue
        if "canonical_trajectory_path" not in src:
            unnormalised.append(str(path.relative_to(root)))

    assert unnormalised == [], f"writes global_projects without normalising trajectory_path: {unnormalised}"


def test_the_repository_writer_normalises_too(tmp_path):
    """The second writer, exercised rather than grepped — a reference to the
    normaliser in a file proves an import, not a call."""
    from empirica.data.repositories.workspace_db import (
        WorkspaceDBRepository,
        _ensure_workspace_schema,
    )

    proj = tmp_path / "repo-proj"
    (proj / ".empirica").mkdir(parents=True)
    conn = sqlite3.connect(str(tmp_path / "ws.db"))
    _ensure_workspace_schema(conn)

    WorkspaceDBRepository(conn).upsert_project(
        project_id="33333333-3333-3333-3333-333333333333",
        name="repo-proj",
        trajectory_path=str(proj),
    )
    stored = conn.execute("SELECT trajectory_path FROM global_projects").fetchone()[0]
    conn.close()

    assert stored == str(proj / ".empirica")

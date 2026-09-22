"""Reconciling historical notes/sqlite divergence.

The write path is correct from 697bd613 forward; everything gardened before it
left notes disagreeing with sqlite. Because `rebuild --qdrant` imports notes back
INTO sqlite, that backlog is a set of pending reverts.

Measured on one practice when this landed: 129 orphaned notes and 973 unstamped
resolutions.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from empirica.core.canonical.empirica_git.note_lifecycle import ACTIVE_PREFIX, ARCHIVE_PREFIX
from empirica.core.canonical.empirica_git.note_reconcile import notes_root, plan


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "r"
    p.mkdir()
    run = lambda *a: subprocess.run(["git", *a], cwd=p, capture_output=True, text=True, check=True)  # noqa: E731
    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (p / "f.txt").write_text("x")
    run("add", ".")
    run("commit", "-qm", "init")
    return p


class _FakeDB:
    """Minimal sqlite stand-in — real tables, so the SQL is genuinely exercised."""

    def __init__(self, tmp_path, live_findings, resolved_findings):
        import sqlite3

        self.conn = sqlite3.connect(str(tmp_path / "s.db"))
        c = self.conn.cursor()
        c.execute(
            "CREATE TABLE project_findings (id TEXT PRIMARY KEY, is_resolved INT, resolution TEXT, resolution_kind TEXT)"
        )
        for fid in live_findings:
            c.execute(
                "INSERT INTO project_findings VALUES (?,?,?,?)",
                (fid, 1 if fid in resolved_findings else 0, "r" if fid in resolved_findings else None, None),
            )
        self.conn.commit()


def _note(repo, atype, aid):
    head = subprocess.run(["git", "rev-list", "-1", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    subprocess.run(
        ["git", "notes", f"--ref=empirica/{atype}/{aid}", "add", "-f", "-m", json.dumps({"finding": aid}), head],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _ref_exists(repo, ref):
    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref], cwd=repo, capture_output=True, text=True
        ).returncode
        == 0
    )


def test_plan_finds_notes_sqlite_no_longer_has(repo, tmp_path):
    for aid in ("a", "b", "ghost"):
        _note(repo, "findings", aid)
    db = _FakeDB(tmp_path, live_findings={"a", "b"}, resolved_findings=set())

    p = plan(db, str(repo))

    assert p["types"]["findings"]["orphaned"] == ["ghost"]
    assert p["orphaned_total"] == 1


def test_plan_finds_resolutions_the_notes_never_received(repo, tmp_path):
    for aid in ("a", "b"):
        _note(repo, "findings", aid)
    db = _FakeDB(tmp_path, live_findings={"a", "b"}, resolved_findings={"b"})

    p = plan(db, str(repo))

    assert p["types"]["findings"]["unstamped"] == ["b"]
    assert p["types"]["findings"]["orphaned"] == [], "a resolved artifact is NOT orphaned — sqlite keeps it"


def test_a_clean_store_plans_NOTHING(repo, tmp_path):
    """Positive control for both finders. Against a plan() that returned every
    note regardless, the two tests above would still pass."""
    for aid in ("a", "b"):
        _note(repo, "findings", aid)
    db = _FakeDB(tmp_path, live_findings={"a", "b"}, resolved_findings=set())

    p = plan(db, str(repo))

    assert p["orphaned_total"] == 0 and p["unstamped_total"] == 0
    assert "findings" in p["types"], "a zero must be a measured zero, not a missing key"


def test_apply_archives_orphans_and_leaves_the_live_ones(repo, tmp_path):
    from empirica.core.canonical.empirica_git.note_reconcile import apply as _apply

    for aid in ("a", "ghost"):
        _note(repo, "findings", aid)
    db = _FakeDB(tmp_path, live_findings={"a"}, resolved_findings=set())

    receipt = _apply(db, str(repo), plan(db, str(repo)))

    assert receipt["archived"] == 1
    assert receipt["failed"] == []
    assert not _ref_exists(repo, f"{ACTIVE_PREFIX}/findings/ghost")
    assert _ref_exists(repo, f"{ARCHIVE_PREFIX}/findings/ghost"), "the journey survives"
    assert _ref_exists(repo, f"{ACTIVE_PREFIX}/findings/a"), "a live artifact's note must be untouched"


def test_worktrees_collapse_onto_ONE_notes_root(repo, tmp_path):
    """The trap autonomy flagged: one practice has 22 worktrees on a single notes
    history. Keying on the working directory reconciles the same store 22 times,
    and a run that quietly skips 21 paths is indistinguishable from one that had
    a single path to begin with.
    """
    wt = tmp_path / "wt"
    subprocess.run(["git", "worktree", "add", "-q", str(wt)], cwd=repo, capture_output=True, text=True, check=True)

    main_root, worktree_root = notes_root(str(repo)), notes_root(str(wt))

    assert main_root and worktree_root
    assert main_root == worktree_root, "a worktree must resolve to the SAME notes store as its main checkout"
    assert str(wt) not in worktree_root, "keying on the working directory is the defect"


def test_notes_root_returns_None_outside_a_repo(tmp_path):
    """Unknown is not a value. A caller must be able to tell 'not a repo' from a
    root it can dedupe on."""
    assert notes_root(str(tmp_path)) is None


def _note_with(repo, atype, aid, payload):
    head = subprocess.run(["git", "rev-list", "-1", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    subprocess.run(
        ["git", "notes", f"--ref=empirica/{atype}/{aid}", "add", "-f", "-F", "-", head],
        input=json.dumps(payload),
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def test_a_note_that_already_carries_the_resolution_is_not_unstamped(repo, tmp_path):
    """The plan read `resolved & note_ids`, so every resolved artifact with a note
    counted as "never received" — 2221 on core, of which 963 really were."""
    _note_with(repo, "findings", "stamped", {"finding": "s", "is_resolved": True})
    _note_with(repo, "findings", "unknown_shape", {"unknown": "u", "resolved": True})
    _note(repo, "findings", "bare")
    db = _FakeDB(
        tmp_path,
        live_findings={"stamped", "unknown_shape", "bare"},
        resolved_findings={"stamped", "unknown_shape", "bare"},
    )

    p = plan(db, str(repo))

    assert p["types"]["findings"]["unstamped"] == ["bare"]


def test_apply_then_plan_converges_to_zero(repo, tmp_path):
    """The property the old count could not have: after a repair, the check clears."""
    from empirica.core.canonical.empirica_git.note_reconcile import apply as _apply

    for aid in ("a", "b"):
        _note(repo, "findings", aid)
    db = _FakeDB(tmp_path, live_findings={"a", "b"}, resolved_findings={"a", "b"})

    first = plan(db, str(repo))
    assert first["unstamped_total"] == 2, "positive control: both start unstamped"
    receipt = _apply(db, str(repo), first)
    assert receipt["stamped"] == 2 and receipt["failed"] == []

    assert plan(db, str(repo))["unstamped_total"] == 0

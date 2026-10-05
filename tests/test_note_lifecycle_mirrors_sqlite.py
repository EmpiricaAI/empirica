"""Gardening must reach notes, and reach them the way sqlite does.

Notes are the canonical log — `rebuild --qdrant` imports them back INTO sqlite —
so a note that disagrees with sqlite is not a stale copy, it is a pending revert.

The two halves needed different fixes, measured 2026-09-06:

    delete-artifacts   DID reach notes but DESTROYED the ref (journey lost)
    *-resolve          reached notes NOT AT ALL (zero references in either handler)

So delete archives and resolve stamps. **Archiving on resolve would be the
obvious-looking mistake** — sqlite keeps resolved rows, so dropping them out of
the active namespace is a second divergence in the opposite direction.

EVERY assertion here is paired with its opposite. The acceptance invariant
"active notes reproduce sqlite" passes trivially on two empty stores, and three
practices shipped a control that could not fire on the day this was designed.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from empirica.core.canonical.empirica_git.note_lifecycle import (
    ACTIVE_PREFIX,
    ARCHIVE_PREFIX,
    archive_note,
    stamp_resolution,
)


@pytest.fixture
def repo(tmp_path):
    """A real git repo with one commit — the notes API needs something to attach to."""
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


def _write_note(repo, atype, aid, payload):
    head = subprocess.run(["git", "rev-list", "-1", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    subprocess.run(
        ["git", "notes", f"--ref=empirica/{atype}/{aid}", "add", "-f", "-m", json.dumps(payload), head],
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


def _read_note(repo, ref):
    """Read a note through the ref:target-commit path.

    A notes ref points at a notes COMMIT whose tree maps target-commit -> blob,
    not at a blob. `<ref>^{blob}` does not resolve — it silently fails on every
    note, which is how the first cut of the helper "passed" its own write and
    failed only when something read the result back.
    """
    short = ref[len("refs/notes/") :]
    listing = subprocess.run(
        ["git", "notes", f"--ref={short}", "list"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    blob_sha = listing.splitlines()[0].split()[0]
    body = subprocess.run(["git", "cat-file", "-p", blob_sha], cwd=repo, capture_output=True, text=True).stdout
    return json.loads(body)


# --------------------------------------------------------------------------
# DELETE: archives, does not destroy
# --------------------------------------------------------------------------


def test_delete_MOVES_the_note_rather_than_destroying_it(repo):
    _write_note(repo, "findings", "f1", {"finding": "x"})
    assert _ref_exists(repo, f"{ACTIVE_PREFIX}/findings/f1"), "positive control: the note was written"

    out = archive_note("findings", "f1", str(repo))

    assert out["archived"] is True
    assert not _ref_exists(repo, f"{ACTIVE_PREFIX}/findings/f1"), "must leave the active namespace"
    assert _ref_exists(repo, f"{ARCHIVE_PREFIX}/findings/f1"), "the journey must survive"
    assert _read_note(repo, f"{ARCHIVE_PREFIX}/findings/f1") == {"finding": "x"}, "content preserved verbatim"


def test_archiving_an_absent_note_reports_NOT_PRESENT_not_success(repo):
    """A delete of something that was never there is a different fact from one
    that worked, and only one of them means the graph changed."""
    out = archive_note("findings", "nope", str(repo))
    assert out["archived"] is False
    assert out["reason"] == "not_present"


# --------------------------------------------------------------------------
# RESOLVE: stamps, stays ACTIVE
# --------------------------------------------------------------------------


def test_resolve_STAMPS_and_the_note_STAYS_ACTIVE(repo):
    """The asymmetry. sqlite keeps resolved rows; archiving here would drop every
    resolved artifact out of the active graph."""
    _write_note(repo, "findings", "f2", {"finding": "y", "impact": 0.5})

    out = stamp_resolution(
        "findings", "f2", {"is_resolved": True, "resolution_kind": "retracted", "resolution": "was wrong"}, str(repo)
    )

    assert out["stamped"] is True
    assert _ref_exists(repo, f"{ACTIVE_PREFIX}/findings/f2"), "a resolved note must NOT be archived"
    assert not _ref_exists(repo, f"{ARCHIVE_PREFIX}/findings/f2")

    note = _read_note(repo, f"{ACTIVE_PREFIX}/findings/f2")
    assert note["is_resolved"] is True
    assert note["resolution_kind"] == "retracted"
    assert note["finding"] == "y", "the original claim is immutable — resolution records closure, not a rewrite"
    assert note["impact"] == 0.5, "unrelated fields must survive the merge"


def test_stamping_refuses_to_overwrite_an_UNREADABLE_payload(repo):
    """A note whose body is not JSON is not ours to rewrite. Reporting the refusal
    beats silently replacing someone's data with our merge."""
    head = subprocess.run(["git", "rev-list", "-1", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    subprocess.run(
        ["git", "notes", "--ref=empirica/findings/f3", "add", "-f", "-m", "not json at all", head],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    out = stamp_resolution("findings", "f3", {"is_resolved": True}, str(repo))
    assert out["stamped"] is False
    assert "not JSON" in out["reason"] or "unreadable" in out["reason"]
    assert (
        "not json at all"
        in subprocess.run(
            ["git", "notes", "--ref=empirica/findings/f3", "show", head], cwd=repo, capture_output=True, text=True
        ).stdout
    )


# --------------------------------------------------------------------------
# THE INVARIANT, with its planted divergence
# --------------------------------------------------------------------------


def _active_ids(repo, atype):
    out = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)", f"{ACTIVE_PREFIX}/{atype}/"],
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout.split()
    return {r.rsplit("/", 1)[-1] for r in out}


def test_the_invariant_FAILS_on_a_planted_divergence_before_it_passes_clean(repo):
    """The acceptance criterion, and the reason this test exists in this shape.

    "Active notes reproduce sqlite" passes trivially on two empty stores, on a
    comparison reading a field neither populates, or on a helper that no-ops.
    Three practices shipped a control that could not fire on the day this was
    designed — a shallow-clone guard proved against a never-shallow clone, a
    probe returning 401 on every path, and controls asserting a warning fired but
    never that anything changed.

    So: PLANT the divergence, assert the comparison SEES it, then remove it and
    assert the comparison goes clean. A green that has never been red is
    indistinguishable from a comparison that cannot see a difference.
    """
    sqlite_live = {"a", "b"}
    for aid in ("a", "b", "noise"):
        _write_note(repo, "findings", aid, {"finding": aid})

    # PLANTED: notes hold `noise`, sqlite does not.
    assert _active_ids(repo, "findings") != sqlite_live, "the divergence must be VISIBLE before the fix"
    assert _active_ids(repo, "findings") - sqlite_live == {"noise"}

    archive_note("findings", "noise", str(repo))

    # And now clean — the same comparison, the opposite verdict.
    assert _active_ids(repo, "findings") == sqlite_live, "active notes must mirror sqlite after gardening"
    assert _ref_exists(repo, f"{ARCHIVE_PREFIX}/findings/noise"), "and the journey is still there"


def test_a_resolved_artifact_stays_in_the_invariant_set(repo):
    """The half that archiving-on-resolve would have broken: sqlite keeps resolved
    rows, so they must remain in the ACTIVE notes set, not leave it."""
    for aid in ("a", "b"):
        _write_note(repo, "findings", aid, {"finding": aid})
    stamp_resolution("findings", "b", {"is_resolved": True}, str(repo))
    assert _active_ids(repo, "findings") == {"a", "b"}, "a resolved note must still be ACTIVE"


def test_an_archive_that_already_holds_different_content_is_not_overwritten(repo):
    """A sync-pull can resurrect an archived note; deleting it again must not replace the earlier archive."""
    run = lambda *a: subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True, check=True)  # noqa: E731
    _write_note(repo, "findings", "f9", {"finding": "newer"})
    (repo / "g.txt").write_text("y")
    run("add", ".")
    run("commit", "-qm", "second")
    _write_note(repo, "findings", "older", {"finding": "earlier archive"})
    older = run("rev-parse", f"{ACTIVE_PREFIX}/findings/older").stdout.strip()
    run("update-ref", f"{ARCHIVE_PREFIX}/findings/f9", older)

    out = archive_note("findings", "f9", str(repo))

    assert out["archived"] is False and "different content" in out["reason"]
    assert _ref_exists(repo, f"{ACTIVE_PREFIX}/findings/f9"), "the active note must be left in place"
    assert run("rev-parse", f"{ARCHIVE_PREFIX}/findings/f9").stdout.strip() == older

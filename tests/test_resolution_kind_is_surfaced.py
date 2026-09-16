"""An omitted `--kind` must not render identically to a classified resolution.

`finding-resolve` requires `finding_id` and `--resolution`; `--kind` is optional
and, when omitted, stores NULL and returned `resolution_kind: null` with no
comment. So the vocabulary shipped and the behaviour never did:

  · 1,344 of 1,604 resolutions in this practice carry no kind at all
  · `mistyped` has been used zero times across 4,833 findings here, and
    empirica-autonomy independently measured 0 of 726, plus 0 `retracted`

Two practices, one core-owned vocabulary — a mechanism problem, not a discipline
one. An optional field that requires a judgement gets skipped under momentum, and
nothing in the receipt ever said a judgement had been skipped.

The fix is deliberately neither obvious option. **Required** breaks every existing
caller and script. **Defaulted** is how a ledger ends up reading 1267 `stale` to 1
`retracted` — a practice that looks as though it was never wrong. What is left is
to make the omission *visible*, at the one moment the practitioner still holds the
judgement.

These tests read the RECEIPT. An earlier draft asserted on the handler's source
text, which would pass if the note existed and never fired — the vacuous-test
shape, in the tests for a fix about things that look identical.
"""

from __future__ import annotations

import contextlib
import io
import json
import uuid
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers.artifact_log_commands import handle_finding_resolve_command
from empirica.data.session_database import SessionDatabase

PROJECT_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway store, with the handler pointed at it rather than at the box."""
    d = SessionDatabase(db_path=str(tmp_path / "resolve.db"))
    # The handler does `from empirica.data.session_database import SessionDatabase`
    # INSIDE the function, so the name must be replaced on the source module — a
    # patch of the handler's namespace would never be consulted.
    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", lambda *a, **kw: d)
    monkeypatch.setattr(type(d), "close", lambda self: None)
    yield d


def _receipt(db, **kw) -> dict:
    fid = db.log_finding(PROJECT_ID, SESSION_ID, f"claim {uuid.uuid4().hex[:8]}")
    fields = {
        "finding_id": fid,
        "resolution": "closed",
        "superseded_by": None,
        "resolution_kind": None,
        "output": "json",
    }
    fields.update(kw)
    args = SimpleNamespace(**fields)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        handle_finding_resolve_command(args)
    out = buf.getvalue()
    # Parse from the first brace, NOT line by line: the receipt is pretty-printed,
    # so no single line is valid JSON. A line-wise parser finds nothing and, if it
    # degrades to a skip, reports the suite as green — which is how four tests for
    # a fix about things-that-look-identical came back as four silent skips.
    start = out.find("{")
    assert start != -1, f"handler produced no JSON receipt; stdout was {out!r}"
    return json.loads(out[start:])


def test_an_unclassified_resolution_says_so(db):
    """The defect, as a test: this receipt used to be silent about the omission."""
    r = _receipt(db)

    assert r["ok"] is True, "the resolution itself must still succeed — this is a note, not a gate"
    assert r.get("resolution_kind") is None
    assert "resolution_kind_note" in r, "an omitted judgement must be visible in the receipt"


def test_the_note_names_every_value_including_the_unused_one(db):
    """A hint that omits a value cannot teach the value it omits.

    `mistyped` is the one with zero uses across two practices, so a note listing
    only the familiar three would leave exactly the gap this exists to close.
    """
    note = _receipt(db)["resolution_kind_note"]

    for kind in ("stale", "superseded", "retracted", "mistyped"):
        assert kind in note, f"the note must name {kind} — an omitted value is one nobody learns"


def test_the_note_names_what_is_lost_not_merely_that_a_flag_exists(db):
    """ "--kind is available" teaches nothing; the missing thing is the WHY."""
    note = _receipt(db)["resolution_kind_note"]

    assert "THAT it closed" in note and "not WHY" in note


def test_a_classified_resolution_gets_no_note(db):
    """The positive control, and the thing that keeps this from becoming noise.

    Without it, the three tests above would all pass on a note attached to every
    resolution — which is the failure mode that trains people to stop reading
    receipts.
    """
    r = _receipt(db, **{"resolution_kind": "retracted"})

    assert r["resolution_kind"] == "retracted"
    assert "resolution_kind_note" not in r, (
        "a practitioner who classified their resolution has already done the thing the note asks for"
    )

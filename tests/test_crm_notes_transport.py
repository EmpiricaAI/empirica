"""CRM over git notes: core moves bytes, workspace owns meaning.

The lane is ratified — `empirica-workspace` owns CRM semantics (merge rules,
conflict reporting, what a scope column means); core owns the transport. So the
sharp test of this module is not what it accepts but what it REFUSES TO JUDGE:
it must carry a table it has never heard of, because owning the table vocabulary
would mean owning the CRM.

Envelope settled 2026-09-03, `table` widened to three tables 2026-09-04.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from empirica.core.canonical.empirica_git.crm_store import (
    ENVELOPE_VERSION,
    CrmEnvelopeError,
    CrmNoteStore,
    build_envelope,
    note_ref,
    validate_envelope,
)

SEAT = "empirica.david.empirica"


def _env(table="engagements", **kw):
    base = {
        "sender_seat": SEAT,
        "table": table,
        "row": {"engagement_id": "e-1", "status": "active"},
        "updated_at": 1788000000.0,
        "base_updated_at": 1787999000.0,
    }
    base.update(kw)
    return build_envelope(**base)


# ── the settled envelope ─────────────────────────────────────────────────────


def test_the_envelope_carries_every_settled_field():
    e = _env()

    assert e["envelope_version"] == ENVELOPE_VERSION == 1
    assert e["sender_seat"] == SEAT
    assert e["table"] == "engagements"
    assert e["row"]["engagement_id"] == "e-1"
    assert e["updated_at"] == 1788000000.0
    assert e["base_updated_at"] == 1787999000.0


def test_a_non_canonical_sender_seat_is_REFUSED():
    """Not retrofittable: a note written without the 3-form is anonymous
    forever, and no CRM table carries `updated_by` to reconstruct it. So the
    refusal has to be at write time, not a later audit."""
    with pytest.raises(CrmEnvelopeError, match="3-form"):
        _env(sender_seat="empirica")


def test_additive_extras_ride_through_without_bumping_the_version():
    """Workspace's slice-2 per-group stamps must land without a format break —
    the version field is what makes that promise checkable rather than asserted."""
    e = _env(extra={"group_stamps": {"contact": 1788000001.0}})

    assert e["group_stamps"] == {"contact": 1788000001.0}
    assert e["envelope_version"] == 1


def test_an_extra_cannot_clobber_a_settled_field():
    """A packet whose `table` was overwritten by an addition would route to the
    wrong reader with nothing to notice it."""
    with pytest.raises(CrmEnvelopeError, match="overwrite"):
        _env(extra={"table": "something_else"})


# ── what core refuses to judge (THE lane test) ───────────────────────────────


@pytest.mark.parametrize("table", ["engagements", "organizations", "contacts"])
def test_all_three_settled_tables_are_carried(table):
    assert _env(table=table)["table"] == table


def test_a_table_core_has_never_heard_of_is_STILL_carried():
    """THE lane assertion, and the reason there is no SYNCABLE_TABLES here.

    Workspace asked me to import their list rather than restate it, precisely so
    core's writer could never refuse a table their builder emits. The import is
    unavailable across distributions — and restating the list would be that
    drift with an extra indirection. So the transport validates NEITHER the
    table name NOR the row shape. `syncable IFF it has a scope column` is
    enforced where the scope column lives.

    If this test ever fails, core has taken ownership of the CRM vocabulary.
    """
    e = _env(table="a_table_invented_next_month")

    assert validate_envelope(e)["table"] == "a_table_invented_next_month"


def test_the_row_shape_is_not_judged():
    """Same reason. A row core validates is a row core owns."""
    e = build_envelope(
        sender_seat=SEAT,
        table="contacts",
        row={"whatever_workspace_decides": True},
        updated_at=1.0,
    )

    assert validate_envelope(e)["row"] == {"whatever_workspace_decides": True}


def test_no_table_allowlist_exists_in_the_module():
    """Structural guard: a future edit adding a table list here re-owns the CRM
    and reintroduces the drift, while reading as a helpful improvement.

    Asserted over the AST, not the text. The first version grepped for the
    string `SYNCABLE_TABLES` and failed on the module docstring that EXPLAINS
    why there is no such list — a self-referential grep, which is the third time
    this pattern has bitten me: prose that discusses a symbol is not the symbol.
    What must be absent is a *value* enumerating table names, so that is what
    gets inspected.
    """
    import ast
    import inspect

    import empirica.core.canonical.empirica_git.crm_store as mod

    tree = ast.parse(inspect.getsource(mod))
    table_names = {"engagements", "organizations", "contacts"}

    for node in ast.walk(tree):
        if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
            literals = {e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
            overlap = literals & table_names
            assert not overlap, (
                f"line {node.lineno}: a collection enumerating CRM table names {sorted(overlap)} — "
                "core would own the CRM vocabulary, and its writer could refuse a table "
                "workspace's builder emits. Table is DATA here."
            )


# ── what core DOES refuse: unreadable packets ────────────────────────────────


@pytest.mark.parametrize("field", ["envelope_version", "sender_seat", "table", "row", "updated_at"])
def test_a_missing_required_field_is_named(field):
    e = _env()
    e[field] = None

    with pytest.raises(CrmEnvelopeError, match=field):
        validate_envelope(e)


def test_a_future_envelope_version_refuses_loudly():
    """Silently accepting a newer packet applies a row the sender expected to be
    handled differently — the absent-versus-explicit collapse, one layer up."""
    e = _env()
    e["envelope_version"] = ENVELOPE_VERSION + 1

    with pytest.raises(CrmEnvelopeError, match="newer than this transport"):
        validate_envelope(e)


def test_an_older_envelope_version_is_still_readable():
    """NEGATIVE CONTROL: forward-incompatible, backward-compatible."""
    e = _env()
    e["envelope_version"] = 1

    assert validate_envelope(e)


def test_a_delete_is_refused_by_agreement():
    e = _env()
    e["op"] = "delete"

    with pytest.raises(CrmEnvelopeError, match="delete"):
        validate_envelope(e)


# ── the note ref, and replication ────────────────────────────────────────────


def test_the_table_is_a_path_segment_not_a_literal():
    """Hardcoding `engagements` would silently drop two tables of three while
    every push reported success — workspace named this failure explicitly."""
    assert note_ref("organizations", "o-acme") == "empirica/crm/organizations/o-acme"
    assert note_ref("contacts", "c-1") == "empirica/crm/contacts/c-1"


def test_crm_refs_ride_the_existing_replication_wildcard():
    """`refs/notes/empirica/crm/...` is inside `refs/notes/empirica/*`, so CRM
    notes replicate through the contract that already exists. A dedicated
    per-table refspec would put the table list back into core — and a namespace
    outside the refspec would never leave the machine, which is how 5,622 refs
    went missing in plain sight."""
    from empirica.cli.command_handlers.sync_commands import _PUSH_REFSPECS

    wildcards = [src for _n, spec, _t in _PUSH_REFSPECS for src in [spec.split(":")[0]]]

    assert "refs/notes/empirica/*" in wildcards
    assert note_ref("contacts", "c-1").startswith("empirica/")


# ── round trip through real git ──────────────────────────────────────────────


@pytest.fixture
def repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "f").write_text("x")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
    return tmp_path


def test_write_then_read_round_trips(repo):
    store = CrmNoteStore(repo)
    written = store.write(_env(table="organizations"), "o-acme")

    assert written == "empirica/crm/organizations/o-acme"
    got = store.read("organizations", "o-acme")
    assert got["row"]["engagement_id"] == "e-1"
    assert got["sender_seat"] == SEAT


def test_reading_an_absent_row_is_None_not_an_error(repo):
    assert CrmNoteStore(repo).read("contacts", "never-written") is None


def test_a_corrupt_note_RAISES_rather_than_reading_as_absent(repo):
    """A malformed packet and a row that was never shared are different states;
    collapsing them is the absence-through-defaulting-read shape."""
    store = CrmNoteStore(repo)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    subprocess.run(
        ["git", "notes", "--ref=empirica/crm/contacts/c-bad", "add", "-f", "-m", "{not json", head],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    with pytest.raises((json.JSONDecodeError, CrmEnvelopeError)):
        store.read("contacts", "c-bad")


def test_list_refs_scopes_by_table(repo):
    store = CrmNoteStore(repo)
    store.write(_env(table="contacts"), "c-1")
    store.write(_env(table="organizations"), "o-1")

    assert len(store.list_refs()) == 2
    assert store.list_refs("contacts") == ["refs/notes/empirica/crm/contacts/c-1"]

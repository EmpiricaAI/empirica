"""`update-artifacts` — the gardening verb that was missing.

`log-artifacts` creates, `resolve-artifacts` closes, `delete-artifacts` removes.
None of them can change a FIELD, so an artifact that is real and correctly typed
but carries a wrong impact score or a contaminated `epistemic_source` had no
correction path at all from the CLI. David: it is *load-bearing for gardening* —
not symmetry for its own sake.

Two invariants carry the design:

1. **The claim TEXT is immutable.** Rewriting what an artifact said would make the
   record unfalsifiable: a reader could not distinguish "this was always the claim"
   from "someone edited it after it was contradicted". Wrong claims take
   `finding-resolve --kind retracted`, which preserves the wording and records the
   failure. Correct the metadata; retract the claim.
2. **Rejected fields are REPORTED.** The daemon's PATCH drops unknown keys quietly,
   defensible for a machine caller and wrong for a practitioner typing a field
   name — a correction that reports success while changing nothing is the
   advertised-and-discarded pattern this whole surface exists to end.
"""

from __future__ import annotations

import io
import json
import sys

import pytest

from empirica.data.artifact_fields import ARTIFACT_TABLES, filter_updates, updatable_fields

# ── the shared field map ──────────────────────────────────────────────


def test_claim_text_is_not_updatable_on_any_type():
    """THE invariant. If any of these ever becomes correctable, the epistemic
    record stops being falsifiable."""
    immutable = {
        "finding": "finding",
        "unknown": "unknown",
        "dead_end": "approach",
        "mistake": "mistake",
        "decision": "choice",
        "assumption": "assumption",
    }
    for atype, claim_field in immutable.items():
        assert claim_field not in updatable_fields(atype), f"{atype}.{claim_field} must stay immutable"


def test_epistemic_source_is_correctable_on_every_artifact_type():
    """Provenance is the field most often wrong in practice — peer-derived
    artifacts tagged `search` as though the practitioner observed the system."""
    for atype in ("finding", "unknown", "dead_end", "mistake", "assumption", "decision"):
        assert "epistemic_source" in updatable_fields(atype)


def test_an_unknown_type_yields_no_updatable_fields():
    assert updatable_fields("not_a_type") == set()


def test_filter_reports_rejected_names_rather_than_dropping_them():
    updates, rejected = filter_updates("finding", {"impact": 0.3, "finding": "rewrite", "nonsense": 1})

    assert updates == {"impact": 0.3}
    assert sorted(rejected) == ["finding", "nonsense"]


def test_the_table_map_names_the_real_assumptions_table():
    """`project_assumptions` was referenced for the life of a verb and never
    existed. The table map is shared precisely so that cannot recur per-consumer."""
    assert ARTIFACT_TABLES["assumption"][0] == "assumptions"
    assert ARTIFACT_TABLES["mistake"][0] == "mistakes_made"


# ── the handler ───────────────────────────────────────────────────────


@pytest.fixture
def wired(tmp_path, monkeypatch):
    import empirica.data.session_database as _sdb

    db_file = str(tmp_path / "t.db")
    real = _sdb.SessionDatabase
    monkeypatch.setattr(_sdb, "SessionDatabase", lambda *a, **k: real(db_path=db_file))

    db = real(db_path=db_file)
    try:
        db.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, "
            "finding_data, impact, epistemic_source) "
            "VALUES ('aaaaaaaa-1111-4000-8000-000000000001','p','s','cortex found X',0.0,'{}',0.9,'search')"
        )
        db.conn.commit()
    finally:
        db.close()
    return db_file, real


def _run(payload) -> dict:
    from empirica.cli.command_handlers.graph_commands import handle_update_artifacts_command

    class _Args:
        config = "-"
        schema = False
        output = "json"
        verbose = False

    buf, out = sys.stdout, io.StringIO()
    stdin, sys.stdin = sys.stdin, io.StringIO(json.dumps(payload))
    sys.stdout = out
    try:
        handle_update_artifacts_command(_Args())
    finally:
        sys.stdin, sys.stdout = stdin, buf
    return json.loads(out.getvalue())


def _col(real, db_file, sql):
    db = real(db_path=db_file)
    try:
        return db.conn.execute(sql).fetchone()[0]
    finally:
        db.close()


def test_provenance_is_corrected(wired):
    """THE use case: a peer-derived artifact mis-tagged as first-hand observation."""
    db_file, real = wired
    aid = "aaaaaaaa-1111-4000-8000-000000000001"

    res = _run({"updates": [{"type": "finding", "id": aid, "epistemic_source": "mixed"}]})

    assert res["updated"] == 1
    assert _col(real, db_file, f"SELECT epistemic_source FROM project_findings WHERE id='{aid}'") == "mixed"


def test_rewriting_the_claim_is_refused_and_REPORTED(wired):
    """Silent rejection would be the advertised-and-discarded pattern. The caller
    must learn the field was not applied."""
    db_file, real = wired
    aid = "aaaaaaaa-1111-4000-8000-000000000001"

    res = _run({"updates": [{"type": "finding", "id": aid, "finding": "a rewritten claim"}]})

    assert res["updated"] == 0
    assert res["rejected_fields"][0]["rejected"] == ["finding"]
    assert "hint" in res
    assert _col(real, db_file, f"SELECT finding FROM project_findings WHERE id='{aid}'") == "cortex found X", (
        "the original wording must survive"
    )


def test_a_valid_field_still_applies_alongside_a_rejected_one(wired):
    """Partial application, with the rejection surfaced — not all-or-nothing
    silence."""
    db_file, real = wired
    aid = "aaaaaaaa-1111-4000-8000-000000000001"

    res = _run({"updates": [{"type": "finding", "id": aid, "impact": 0.3, "finding": "nope"}]})

    assert res["updated"] == 1
    assert res["rejected_fields"][0]["rejected"] == ["finding"]
    assert _col(real, db_file, f"SELECT impact FROM project_findings WHERE id='{aid}'") == 0.3


def test_a_short_id_prefix_is_refused(wired):
    """Same floor as goal-id resolution — a short prefix that happens to be unique
    is not safe, it is lucky."""
    res = _run({"updates": [{"type": "finding", "id": "aa", "impact": 0.1}]})

    assert res["updated"] == 0
    assert any("shorter than 8" in e for e in res["errors"])


def test_a_missing_artifact_reports_not_found(wired):
    res = _run({"updates": [{"type": "finding", "id": "deadbeef-0000-4000-8000-000000000000", "impact": 0.1}]})

    assert res["updated"] == 0
    assert any("not found" in e for e in res["errors"])


def test_an_unknown_type_is_refused_by_name(wired):
    res = _run({"updates": [{"type": "banana", "id": "aaaaaaaa-1111-4000-8000-000000000001", "impact": 0.1}]})

    assert res["updated"] == 0
    assert any("banana" in e for e in res["errors"])


def test_an_empty_updates_array_is_refused(wired):
    res = _run({"updates": []})

    assert res["ok"] is False


def test_multiple_artifacts_update_in_one_call(wired):
    db_file, real = wired
    db = real(db_path=db_file)
    try:
        db.conn.execute(
            "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, "
            "finding_data, epistemic_source) "
            "VALUES ('bbbbbbbb-1111-4000-8000-000000000002','p','s','another',0.0,'{}','search')"
        )
        db.conn.commit()
    finally:
        db.close()

    res = _run(
        {
            "updates": [
                {"type": "finding", "id": "aaaaaaaa-1111-4000-8000-000000000001", "epistemic_source": "mixed"},
                {"type": "finding", "id": "bbbbbbbb-1111-4000-8000-000000000002", "epistemic_source": "intuition"},
            ]
        }
    )

    assert res["updated"] == 2

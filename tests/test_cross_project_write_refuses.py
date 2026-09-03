"""An unresolvable `--project-id` must REFUSE, not write the row somewhere.

The old path logged *"Could not resolve project X, using local DB"* and then wrote
the artifact to the local database **still carrying the unresolved string as its
project_id**. The message was wrong twice over: it did not use the local project (the
row landed under a foreign key), and it reported a fallback as though a fallback were
safe.

The row is then invisible to every project view — not the caller's, not any
registered one — and the command **exits 0**. A typo in a project name silently
strands an artifact.

Decision-downgraded-across-a-boundary: an inability to resolve is a DENY, and it was
degrading to a silent ALLOW. Reported with a live repro by a peer whose side does
keyed read-back verification after every cross-project write — which is the only
reason anyone ever saw it.

**Two sites, not one.** `goal_commands` carried the identical `if cross_db:`-with-no-
else shape; the reporter only hit the artifact one.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.artifact_log_commands import (
    UnresolvableProjectError,
    _resolve_db_for_artifact,
    project_refusal,
)


def test_an_unresolvable_name_raises_instead_of_falling_back():
    """THE regression. Before this it returned (local_db, '<the typo>') and the caller
    wrote a row nobody can see."""
    with pytest.raises(UnresolvableProjectError):
        _resolve_db_for_artifact("definitely-not-a-registered-project-xyz")


@pytest.fixture
def no_real_db(monkeypatch):
    """Stub SessionDatabase so these assert the ROUTING DECISION, not that a database
    opens.

    The first version of these two tests constructed a real `SessionDatabase()`, which
    resolves a live sessions.db path — so they passed on a developer laptop for exactly
    the reason they could never pass in CI, and put the trunk red. The property under
    test was never "a database exists"; it was "this input is not treated as a
    cross-project name lookup".
    """
    import empirica.data.session_database as sdb

    class _Stub:
        db_path = ":memory:"

        def close(self):
            pass

    monkeypatch.setattr(sdb, "SessionDatabase", lambda *a, **k: _Stub())
    return _Stub


def test_a_uuid_is_passed_through_untouched(no_real_db):
    """POSITIVE CONTROL. A UUID is not a name lookup — refusing it would break every
    ordinary local write, which is the failure mode worse than the bug."""
    uuid = "748a81a2-ac14-45b8-a185-994997b76828"
    _db, resolved = _resolve_db_for_artifact(uuid)

    assert resolved == uuid


def test_no_project_id_still_resolves_locally(no_real_db):
    """NEGATIVE CONTROL. The overwhelmingly common call passes no --project-id at all
    and must be untouched by a cross-project guard."""
    _db, resolved = _resolve_db_for_artifact(None)

    assert resolved is None


def test_the_refusal_names_what_actually_exists():
    """A refusal that says only 'could not resolve' sends the reader hunting for the
    spelling. It must answer that from the registry."""
    payload = project_refusal("typo-name")

    assert payload["ok"] is False
    assert payload["requested"] == "typo-name"
    assert "REFUSING" in payload["error"]
    assert isinstance(payload["available_projects"], list)
    assert "Registered projects:" in payload["hint"]


def test_the_refusal_explains_why_falling_back_is_not_safe():
    """The old behaviour looked like graceful degradation. The message has to say why
    it is not — otherwise the next person restores the fallback as a kindness."""
    assert "strand" in project_refusal("x")["error"].lower()


def test_both_write_paths_refuse_not_just_the_reported_one():
    """THE class. `goal_commands` had the same `if cross_db:` with no else — an
    unresolvable target fell through to the LOCAL repository with the unresolved
    string intact. The reporter only hit the artifact path; fixing one and shipping
    would have left a stranded-goal defect behind a closed bug report.

    Asserted structurally: both call sites must reach the refusal, so a third writer
    added later fails here rather than reintroducing the fallback.
    """
    import re
    from pathlib import Path

    root = Path(__file__).parent.parent / "empirica"
    offenders = []
    for path in root.rglob("*.py"):
        src = path.read_text()
        for m in re.finditer(r"_get_db_for_project\(", src):
            if src[max(0, m.start() - 4) : m.start()] == "def ":
                continue  # the definition itself, not a call site
            if "UnresolvableProjectError" not in src[m.end() : m.end() + 400]:
                offenders.append(f"{path.relative_to(root)}:{src[: m.start()].count(chr(10)) + 1}")

    assert offenders == [], f"cross-project resolution without a refusal path: {offenders}"


def test_the_class_check_is_live():
    """POSITIVE CONTROL on the check above. A scan whose pattern matched nothing would
    report a clean sweep forever — and an absence found by an unproven instrument is
    not evidence. This asserts it finds the call sites it is meant to police."""
    import re
    from pathlib import Path

    root = Path(__file__).parent.parent / "empirica"
    call_sites = sum(
        1
        for path in root.rglob("*.py")
        for m in re.finditer(r"_get_db_for_project\(", path.read_text())
        if path.read_text()[max(0, m.start() - 4) : m.start()] != "def "
    )

    assert call_sites >= 2, f"expected both known cross-project call sites, found {call_sites}"


# ── the UUID hole (third trap in the same family) ────────────────────────────


def test_a_foreign_uuid_routes_instead_of_writing_locally(monkeypatch):
    """THE third regression, and the sharpest of the three.

    The guard was `not _is_uuid(project_id)`, so passing a project's canonical UUID —
    the MOST precise identifier, the one you reach for when you want certainty —
    skipped routing entirely and wrote to the LOCAL database with the target's
    project_id stamped on the row.

    The write succeeded and a caller-side read-back keyed on project_id PASSED, while
    a session inside the target project read its own sessions.db and never saw the
    artifact. A peer's prevention experiment ran cold because the primed subject was
    never exposed to a prior they had verified was there.
    """
    import empirica.cli.command_handlers.artifact_log_commands as mod

    foreign = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    routed = {}

    class _Target:
        db_path = "/target/.empirica/sessions/sessions.db"

        def resolve_project_id(self, pid):
            routed["asked"] = pid
            return foreign

        def close(self):
            pass

    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "SessionDatabase", lambda *a, **k: type("S", (), {"close": lambda s: None})())
    monkeypatch.setattr(mod, "_project_exists_locally", lambda db, pid: False)
    monkeypatch.setattr(mod, "_get_db_for_project", lambda pid: _Target())

    db, resolved = _resolve_db_for_artifact(foreign)

    assert routed["asked"] == foreign, "a UUID must reach the cross-project resolver"
    assert resolved == foreign
    assert db.db_path.startswith("/target/"), "and the write must land in the TARGET db"


def test_the_local_uuid_does_not_become_a_refusal(monkeypatch):
    """NEGATIVE CONTROL, and the regression this fix could plausibly have caused.

    Routing every UUID would refuse a write that works today on any box whose own
    project has no `global_projects` row — an unregistered local project is a normal
    state. The local id is answered from the local database, before the registry is
    consulted at all.
    """
    import empirica.cli.command_handlers.artifact_log_commands as mod

    local = "11111111-2222-3333-4444-555555555555"

    def _boom(pid):
        raise AssertionError("the registry must not be consulted for the local project")

    monkeypatch.setattr(mod, "_project_exists_locally", lambda db, pid: True)
    monkeypatch.setattr(mod, "_get_db_for_project", _boom)
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "SessionDatabase", lambda *a, **k: type("S", (), {"close": lambda s: None})())

    _db, resolved = _resolve_db_for_artifact(local)

    assert resolved == local


def test_an_unresolvable_UUID_refuses_like_an_unresolvable_name(monkeypatch):
    """The refusal must not be name-only. A UUID that resolves nowhere is the same
    stranded-row risk as a typo'd name."""
    import empirica.cli.command_handlers.artifact_log_commands as mod
    import empirica.data.session_database as sdb

    monkeypatch.setattr(sdb, "SessionDatabase", lambda *a, **k: type("S", (), {"close": lambda s: None})())
    monkeypatch.setattr(mod, "_project_exists_locally", lambda db, pid: False)
    monkeypatch.setattr(mod, "_get_db_for_project", lambda pid: None)

    with pytest.raises(UnresolvableProjectError):
        _resolve_db_for_artifact("99999999-9999-9999-9999-999999999999")

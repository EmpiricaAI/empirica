"""Five places core misread a Cortex answer, found by cortex building contract/routes.yaml from core's call sites
(prop_ezqtrzl7hvag5j25rrh6wwxdhq; core goal 8fe43c08). Each answer is tested beside the near miss it must still be told
apart from.
"""

from __future__ import annotations

from empirica.cli.command_handlers import projects_commands as pc

UUID = "11111111-2222-3333-4444-555555555555"


# ── D2: a 409 slug_taken is a collision, not "already registered" ─────────────────────────────────────────────────────────


def test_a_slug_collision_is_not_reported_as_registered():
    out = pc._interpret_cortex_register_response(409, {"error": "slug_taken", "slug": "demo"}, UUID)
    assert out["ok"] is False and out["outcome"] == "slug_taken"


def test_an_ordinary_409_is_still_already_registered():
    out = pc._interpret_cortex_register_response(409, {"project_id": UUID}, UUID)
    assert out["ok"] is True and out["outcome"] == "already_registered"


# ── D5: a system event accepted with 202 was emitted ─────────────────────────────────────────────────────────────────────


def test_a_202_from_system_events_reads_as_emitted(monkeypatch, capsys):
    import empirica.cli.command_handlers.compliance_report_commands as cr

    def fake_emit(_envelope):
        return 202, {"accepted": True}

    import empirica.cli.command_handlers.system_event as client  # where emit_system_event lives

    monkeypatch.setattr(client, "emit_system_event", fake_emit, raising=False)
    cr._emit_compliance_to_system_events({"score": 1.0, "checks": []})
    out = capsys.readouterr().out
    assert "emitted" in out and "failed" not in out


# ── D3: a recipient repeating its own archive is already archived, not failed ────────────────────────────────────────────


def test_a_repeat_archive_by_the_same_recipient_succeeds():
    from empirica.cli.command_handlers.mailbox_commands import _archive_succeeded

    assert _archive_succeeded(200, {"error": "already_archived", "is_archived": False}) is True
    assert _archive_succeeded(200, {"is_archived": True, "error": "x"}) is True
    assert _archive_succeeded(200, {"ok": True}) is True


def test_other_errors_and_statuses_still_fail():
    from empirica.cli.command_handlers.mailbox_commands import _archive_succeeded

    assert _archive_succeeded(200, {"error": "not_found"}) is False
    assert _archive_succeeded(200, {"ok": False}) is False
    assert _archive_succeeded(403, {"error": "already_archived"}) is False
    assert _archive_succeeded(200, None) is False


# ── D4: an identical propose still in flight is landing, not refused ─────────────────────────────────────────────────────


def test_in_flight_is_told_apart_from_a_key_conflict():
    from empirica.cli.command_handlers.mailbox_commands import _in_flight

    assert _in_flight(409, {"error": "idempotency_key_in_flight"}) is True
    assert _in_flight(409, {"error": "idempotency_key_conflict"}) is False  # a real collision stays a refusal
    assert _in_flight(200, {"error": "idempotency_key_in_flight"}) is False


# ── D6: a sync for a practice cortex does not know is dropped, and POSTFLIGHT says so ────────────────────────────────────


def test_an_unknown_project_sync_refusal_becomes_a_warning_with_the_fix():
    from empirica.cli.command_handlers._workflow_postflight import _sync_unregistered_message

    msg = _sync_unregistered_message(400, '{"error": "Unknown project_id"}')
    assert msg and "project-register" in msg


def test_other_sync_errors_are_not_called_unregistered():
    from empirica.cli.command_handlers._workflow_postflight import _sync_unregistered_message

    assert _sync_unregistered_message(400, '{"error": "bad payload"}') is None
    assert _sync_unregistered_message(500, "Unknown project_id") is None

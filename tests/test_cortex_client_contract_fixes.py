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


# ── doctor reads cortex's capability document, not just any answer ──────────────────────────────────────────────────────


def test_doctor_passes_on_the_capability_document_and_warns_without_it(monkeypatch):
    import json

    from empirica.cli.command_handlers import doctor

    monkeypatch.setattr(doctor, "_resolve_cortex_creds", lambda: ("https://c.example", None))
    doc = {"edition": "cortex", "contract": "1.0", "capabilities": ["mailbox", "roster"]}
    monkeypatch.setattr(doctor, "_http_get", lambda url, *a, **k: (200, json.dumps(doc)))
    ok = doctor.check_cortex_reachability()
    assert ok.status == "PASS" and "2 capabilities" in ok.detail

    monkeypatch.setattr(doctor, "_http_get", lambda url, *a, **k: (404, "not found"))
    assert doctor.check_cortex_reachability().status == "WARN"
    monkeypatch.setattr(doctor, "_http_get", lambda url, *a, **k: (200, "<html>hello</html>"))
    assert doctor.check_cortex_reachability().status == "WARN"


def test_bulk_and_sync_registration_also_report_a_slug_collision_as_failed(monkeypatch):
    """The single register path learned D2; the per-project loop used by bulk-register and sync still said skipped
    (1.15.0 sweep, T4)."""
    monkeypatch.setattr(pc, "_post_project", lambda *a, **k: (409, {"error": "slug_taken", "slug": "demo"}))
    out = pc._register_one_project({"name": "demo"}, "http://x", "k", 1.0)
    assert out["outcome"] == "failed" and "slug_taken" in out["reason"]


def test_bulk_registration_still_skips_a_project_already_registered(monkeypatch):
    monkeypatch.setattr(pc, "_post_project", lambda *a, **k: (409, {"project_id": UUID}))
    monkeypatch.setattr(pc, "_link_user_to_project", lambda *a, **k: None, raising=False)
    out = pc._register_one_project({"name": "demo"}, "http://x", "k", 1.0)
    assert out["outcome"] == "skipped" and out["reason"] == "already_exists"


def test_the_human_postflight_output_shows_its_warnings(monkeypatch, capsys):
    from empirica.cli.command_handlers import _workflow_postflight as wp

    monkeypatch.setattr(wp, "_postflight_print_project_context", lambda *_a: None)
    result = {"ok": True, "warnings": ["cortex sync dropped: cortex does not know this practice"]}
    wp._postflight_format_human_output(result, "s" * 36, {}, "", {}, [], None)
    assert "cortex sync dropped" in capsys.readouterr().out


def test_slug_taken_names_the_holder_and_the_suggested_name():
    # Cortex's 409 carries existing_id and suggested; dropping them left the user to guess what to rename to (7683668e).
    body = {"code": "slug_taken", "existing_id": "other-id", "suggested": "demo-2"}
    out = pc._interpret_cortex_register_response(409, body, UUID)
    assert out["outcome"] == "slug_taken" and out["existing_id"] == "other-id" and out["suggested"] == "demo-2"
    assert "demo-2" in out["hint"]


def test_bulk_register_reports_the_suggested_name(monkeypatch):
    monkeypatch.setattr(
        pc, "_post_project", lambda *a, **k: (409, {"code": "slug_taken", "existing_id": "x", "suggested": "demo-2"})
    )
    out = pc._register_one_project({"name": "demo"}, "http://x", "k", 1.0)
    assert out["outcome"] == "failed" and out.get("existing_id") == "x" and out.get("suggested") == "demo-2"

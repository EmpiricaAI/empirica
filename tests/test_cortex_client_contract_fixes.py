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

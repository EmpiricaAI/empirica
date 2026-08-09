"""The POSTFLIGHT retrospective must see SER ack debt — the class its
proposal-goal check cannot.

The deferred-proposals note matches goals titled `Process proposal prop_%`. SER
acks are not proposals and carry no such goal, so while that check ran clean,
ser_a705318d accumulated 2888 minutes of unacked debt against this practice and
reached `system:ser-auto-block` — found only because a peer escalated by hand.

Hot-path contract: cortex is reached best-effort with a hard timeout, and every
skip is LEGIBLE via `retro["ser_ack_check"]` rather than silent. A bare
`except: pass` here would reproduce the silent fail-open fixed in e520028f9 the
same day.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers import _workflow_shared as ws


@pytest.fixture
def project_yaml(tmp_path, monkeypatch):
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text("ai_id: empirica\ncanonical_seat: empirica.david.empirica\n")
    monkeypatch.chdir(tmp_path)


def _ser(ser_id, state, role, last_ack, last_tx, practice="empirica.david.empirica"):
    return {
        "ser_id": ser_id,
        "coordination_state": state,
        "last_transition_at": last_tx,
        "participants": [{"practice_id": practice, "role": role, "last_ack_at": last_ack}],
    }


def _run(monkeypatch, sers, status=200):
    monkeypatch.setattr(
        "empirica.cli.command_handlers.mailbox_commands._default_resolve_cortex_creds",
        lambda: ("https://cortex.example", "key"),
    )
    monkeypatch.setattr(
        "empirica.cli.command_handlers.mailbox_commands._default_http_get",
        lambda _url, _key, _t: (status, {"sers": sers}),
    )
    retro: dict = {}
    ws._maybe_add_ser_ack_debt_note(retro)
    return retro


def test_unacked_required_row_is_surfaced(project_yaml, monkeypatch):
    """THE GAP. 2888 minutes of exactly this were invisible to POSTFLIGHT."""
    retro = _run(monkeypatch, [_ser("ser_x", "in_progress", "required", None, "2026-08-08T10:00:00Z")])
    assert retro["ser_ack_check"] == "ok"
    assert retro["ser_acks_owed_count"] == 1
    assert "ser_x" in retro["ser_acks_owed_note"]


def test_stale_ack_counts_as_debt(project_yaml, monkeypatch):
    retro = _run(monkeypatch, [_ser("ser_y", "open", "required", "2026-08-01T00:00:00Z", "2026-08-08T10:00:00Z")])
    assert retro["ser_acks_owed_count"] == 1


def test_current_ack_is_not_debt(project_yaml, monkeypatch):
    """Negative control from the goal's success criteria."""
    retro = _run(monkeypatch, [_ser("ser_z", "open", "required", "2026-08-09T00:00:00Z", "2026-08-08T10:00:00Z")])
    assert retro["ser_ack_check"] == "ok"
    assert "ser_acks_owed_count" not in retro


def test_closed_sers_never_surface(project_yaml, monkeypatch):
    """Closed SERs never escalate — reporting them would be nagging on the dead."""
    retro = _run(monkeypatch, [_ser("ser_c", "closed", "required", None, "2026-08-08T10:00:00Z")])
    assert "ser_acks_owed_count" not in retro


def test_non_required_roles_never_surface(project_yaml, monkeypatch):
    """Only required-tier rows escalate on silence; participating/observer acks
    are opt-in, and nagging on them trains the note to be skipped."""
    retro = _run(monkeypatch, [_ser("ser_p", "open", "participating", None, "2026-08-08T10:00:00Z")])
    assert "ser_acks_owed_count" not in retro


def test_a_cortex_failure_is_a_legible_skip_not_silence(project_yaml, monkeypatch):
    """The e520028f9 lesson applied in advance: handled must also be legible."""
    retro = _run(monkeypatch, [], status=503)
    assert retro["ser_ack_check"] == "skipped (cortex returned 503)"
    assert "ser_acks_owed_count" not in retro


def test_missing_credentials_skip_legibly(project_yaml, monkeypatch):
    monkeypatch.setattr(
        "empirica.cli.command_handlers.mailbox_commands._default_resolve_cortex_creds",
        lambda: (None, None),
    )
    retro: dict = {}
    ws._maybe_add_ser_ack_debt_note(retro)
    assert retro["ser_ack_check"] == "skipped (no cortex credentials)"


def test_no_canonical_seat_skips_legibly(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # no project.yaml at all
    retro: dict = {}
    ws._maybe_add_ser_ack_debt_note(retro)
    assert "canonical_seat" in retro["ser_ack_check"]


def test_an_exception_lands_in_the_retro_not_the_void(project_yaml, monkeypatch):
    def _boom(*_a):
        raise OSError("network down")

    monkeypatch.setattr("empirica.cli.command_handlers.mailbox_commands._default_http_get", _boom)
    monkeypatch.setattr(
        "empirica.cli.command_handlers.mailbox_commands._default_resolve_cortex_creds",
        lambda: ("https://cortex.example", "key"),
    )
    retro: dict = {}
    ws._maybe_add_ser_ack_debt_note(retro)
    assert "OSError" in retro["ser_ack_check"], "a persistent failure must be visible from the retrospective"

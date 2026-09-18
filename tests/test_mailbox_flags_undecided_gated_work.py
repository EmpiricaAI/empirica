"""mailbox show / poll flag gated work with no ECO decision recorded.

Measured 2026-09-18: two TACTICAL code changes to core read as accepted and
were completed with decided_by_kind=system, eco_decision=null (autonomy triage
accepted them); on another practice three public posts went out the same way.
status=accepted is not evidence a human accepted. The flag must be in front of
the practitioner at read time.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from empirica.cli.command_handlers.mailbox_commands import (
    _decision_gap,
    _poll_human_line,
    handle_mailbox_show_command,
)


def _p(**kw):
    base = {
        "id": "prop_x",
        "type": "code_change_request",
        "trust_required": "TACTICAL",
        "status": "completed",
        "decided_by_kind": "system",
        "eco_decision": None,
        "title": "t",
        "source_claude": "empirica.david.empirica-autonomy",
    }
    base.update(kw)
    return base


def test_system_decided_tactical_work_is_flagged():
    assert _decision_gap(_p()) is not None
    assert "NOT HUMAN-DECIDED" in _poll_human_line(_p())


def test_negative_controls_are_not_flagged():
    assert _decision_gap(_p(decided_by_kind="human", eco_decision={"decision": "accept"})) is None
    # decided_by_kind reads "system" for publish rows a human decided in the ECO pane
    # (cortex, prop_zvbpj7z67zbfhl3rm4ng7qbw6m); a recorded eco_decision must win.
    assert (
        _decision_gap(_p(decided_by_kind="system", eco_decision={"decision": "accept", "actor": "eco-phone"})) is None
    )
    assert _decision_gap(_p(status="eco_review", decided_by_kind=None)) is None  # held: waiting is correct
    assert _decision_gap(_p(type="collab_brief")) is None  # auto-accepted by design, no praxic act
    assert _decision_gap(_p(trust_required="REFLEX", action_category="REFLEX")) is None


def test_show_puts_the_warning_in_the_payload_and_on_stderr(capsys):
    args = SimpleNamespace(proposal_id="prop_x", output="json")
    rc = handle_mailbox_show_command(
        args,
        _resolve_cortex_creds=lambda: ("https://cortex.example", "k"),
        _fetch_parent=lambda *_: _p(),
    )
    out, err = capsys.readouterr()
    assert rc == 0
    assert "decision_warning" in json.loads(out)
    assert "NO ECO decision recorded" in err

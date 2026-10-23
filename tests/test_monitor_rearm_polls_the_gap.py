"""Re-arming a Monitor does not deliver what arrived while it was expired, so the arm text must say to poll (goal 87772ac4).

The arm command is `tail -F -n 0 loop_fires.log | grep ...`. `-n 0` starts at the END of the log, so a Monitor armed after an
expiry sees only lines written from then on. Measured 2026-10-23 with that exact tail on a scratch file: a line written while one
tail ran was delivered; two lines written between that tail ending and the next one starting were not; a line written after the
re-arm was. The persistent listener service does keep receiving in the gap, so the lines exist in the log, but the session never
sees them.

Three texts told the practitioner that nothing is lost in the gap. For a session that only listens on the Monitor that is false, and
the cost is a mailbox message that stays unread until someone happens to poll. The one check that does not depend on the stream is
`empirica mailbox poll`, so every arm and re-arm instruction names it.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from empirica.cli.command_handlers import cockpit_commands

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "empirica" / "plugins" / "claude-code-integration"
FALSE_CLAIM = re.compile(r"nothing is lost", re.IGNORECASE)
POLL_RULE = "After every (re)arm, run `empirica mailbox poll`"


def _hook():
    spec = importlib.util.spec_from_file_location("session_monitor_arm", PLUGIN / "hooks" / "session-monitor-arm.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize("payload", [None, {"ok": True, "status": "persistent_service_tail_session"}])
def test_the_arm_block_does_not_promise_a_gap_with_nothing_lost(payload):
    block = _hook()._build_monitor_block_from_cli(payload, "myai")
    assert not FALSE_CLAIM.search(block), (
        "the tail starts at the end of the log; gap lines are not delivered to the session"
    )


@pytest.mark.parametrize("payload", [None, {"ok": True, "status": "persistent_service_tail_session"}])
def test_the_arm_block_tells_the_session_to_poll_after_every_arm(payload):
    block = _hook()._build_monitor_block_from_cli(payload, "myai")
    assert POLL_RULE in block, "the poll must be an instruction attached to every arm, not a passing mention"


def test_the_inbox_listener_skill_does_not_promise_it_either():
    text = " ".join(
        (PLUGIN / "skills" / "inbox-listener" / "SKILL.md").read_text().split()
    )  # the skill wraps its lines
    assert not FALSE_CLAIM.search(text)
    assert POLL_RULE in text


def test_the_listener_registration_expiry_note_does_not_promise_it():
    src = Path(cockpit_commands.__file__).read_text()
    expiry = src[src.index('"expiry": (') : src.index('"after_arm": f"empirica listener arm')]
    assert not FALSE_CLAIM.search(expiry)
    assert POLL_RULE in expiry

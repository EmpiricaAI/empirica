"""Loops key on the PRACTICE (ai_id), not the ephemeral seat.

`_require_loop_key` is the seam every `empirica loop` handler resolves through:
loops belong to the practice (one cortex-mailbox-poll / message-cleanup per
ai_id), keyed like the persistent listener (docs/architecture/AI_ID_AS_ANCHOR.md).
Keying on the practice is what makes loop timer units orphan-proof — the key
doesn't die when the pane closes — and stops per-pane duplicate timers.
"""

from __future__ import annotations

from types import SimpleNamespace

from empirica.cli.command_handlers.cockpit_commands import _require_loop_key
from empirica.core.loop_scheduler.launchd import is_ephemeral_instance


def test_explicit_practice_key_returned_as_is():
    """An explicit non-ephemeral --instance (a practice ai_id, which is what the
    cockpit TUI resolves and passes) is used verbatim — no ambient resolution."""
    assert _require_loop_key(SimpleNamespace(instance="empirica")) == "empirica"
    assert _require_loop_key(SimpleNamespace(instance="empirica-workspace")) == "empirica-workspace"
    assert _require_loop_key(SimpleNamespace(instance="cortex")) == "cortex"


def test_ephemeral_seats_are_not_practice_keys():
    """Seat ids never satisfy the explicit-practice-key fast path — they'd fall
    through to ai_id resolution instead of being used as the loop key."""
    for seat in ("tmux_12", "pts_3", "term_pts_6", "x11_78940210"):
        assert is_ephemeral_instance(seat)
    for practice in ("empirica", "empirica-workspace", "cortex", "ecodex"):
        assert not is_ephemeral_instance(practice)

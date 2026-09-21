"""Which model is the practitioner, right now.

Calibration accrues to the practitioner and artifacts to the practice (David,
2026-09-21), so a calibration row has to say who was being measured. `ai_id`
cannot: it names the practice, and one practice is inhabited by several models,
sometimes within a single session.

The source is the Claude Code transcript. Every assistant line carries
`message.model`. The SessionStart hook payload can carry a model too, but it is
not always sent, and it is a property of the session's start: a transcript on
this box changes model partway through, so the answer belongs to a transaction
and is read when the transaction closes.

Nothing here raises. No transcript, an unreadable one, or one with no assistant
line yet all return None, which is stored as NULL and means "not recorded". It
is never replaced by a default, because a guessed practitioner is worse than an
absent one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Enough for many turns; the last assistant line is what is wanted.
_TAIL_BYTES = 512 * 1024

# Claude Code writes this for lines it generated itself (interrupts, errors).
_NOT_A_MODEL = {"", "<synthetic>"}


def transcript_path(claude_session_id: str | None, home: Path | None = None) -> Path | None:
    """The transcript for a Claude Code session id, wherever its project folder is."""
    if not claude_session_id or "/" in claude_session_id or ".." in claude_session_id:
        return None
    root = (home or Path.home()) / ".claude" / "projects"
    try:
        matches = sorted(root.glob(f"*/{claude_session_id}.jsonl"), key=lambda p: p.stat().st_mtime)
    except OSError as exc:
        logger.debug("practitioner model: cannot list %s (%s)", root, exc)
        return None
    return matches[-1] if matches else None


def model_from_transcript(path: Path | None) -> str | None:
    """The model on the last assistant line of a transcript, or None."""
    if path is None:
        return None
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - _TAIL_BYTES))
            tail = fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        logger.debug("practitioner model: cannot read %s (%s)", path, exc)
        return None

    for line in reversed(tail.splitlines()):
        if '"assistant"' not in line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # the first line of a tail is usually cut mid-record
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message")
        model = message.get("model") if isinstance(message, dict) else None
        if isinstance(model, str) and model not in _NOT_A_MODEL:
            return model
    return None


def current_practitioner_model(claude_session_id: str | None, home: Path | None = None) -> str | None:
    return model_from_transcript(transcript_path(claude_session_id, home))

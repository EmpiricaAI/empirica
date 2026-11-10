"""Wake-up for empirica-local (spec phase 1.4): core's own ``proposal_event`` lines in ``~/.empirica/loop_fires.log``.

On Cortex, ntfy wakes a listener that writes these lines; the session's Monitor tails the file. Locally the server is
on the same machine as the sessions, so it writes the line itself and the same Monitor delivers it: no ntfy, no
listener, no second consumer to keep alive. ``instance_id`` is the practice's short name, which is what the Monitor
filters on.

Emission is best-effort for the request (a proposal is stored whether or not its wake line lands) but never silent: a
failed write is logged as a warning and counted in :data:`FAILURES`, which the capability document reports.
"""

import logging
from pathlib import Path

from empirica.core.loop_scheduler.content_poll import build_event
from empirica.core.loop_scheduler.listener import _rotate_fires_log_if_oversized
from empirica.core.orchestration.schemas import Proposal

logger = logging.getLogger(__name__)
LOOP = "local-server"
FAILURES = {"count": 0, "last_error": None}


def fires_log() -> Path:
    return Path.home() / ".empirica" / "loop_fires.log"


def _short(address: str) -> str:
    return address.rsplit(".", 1)[-1]


def _write(lines: list[str]) -> None:
    log = fires_log()
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        _rotate_fires_log_if_oversized(log)
        with log.open("a", encoding="utf-8") as f:
            f.writelines(line + "\n" for line in lines)
    except OSError as e:
        FAILURES["count"] += 1
        FAILURES["last_error"] = str(e)
        logger.warning("empirica-local: wake line not written to %s: %s", log, e)


def proposed(proposal: Proposal) -> None:
    """Wake every target of a newly stored proposal."""
    wire = proposal.to_wire()
    _write(
        [
            build_event(wire, "new", _short(t), LOOP, direction="inbox").to_log_line()
            for t in proposal.distinct_targets()
        ]
    )


def finished(proposal: Proposal) -> None:
    """Wake the source of a proposal that has just turned terminal."""
    if proposal.source_claude:
        event = build_event(
            proposal.to_wire(), "status_changed", _short(proposal.source_claude), LOOP, direction="outbox"
        )
        _write([event.to_log_line()])

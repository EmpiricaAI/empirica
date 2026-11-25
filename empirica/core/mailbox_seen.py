"""Which mesh messages a practice has read, recorded on this machine (goal f5148923).

Cortex keeps no read marker on a proposal, so an FYI collab_brief, which needs no reply, stayed in the cockpit's mail count
until someone archived it. `empirica mailbox show` records the id here for the practice reading it, and the cockpit's
count drops a collab_brief once read (David, 2026-11-25). Best-effort: a store that cannot be read is empty, and a write
that fails changes nothing but the count.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

MAX_IDS = 1000


def _path(ai_id: str, home: Path | None = None) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", ai_id) or "_"
    return (home or Path.home()) / ".empirica" / "mailbox_seen" / f"{safe}.json"


def _read(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = data.get("ids") if isinstance(data, dict) else None
        return {str(k): float(v) for k, v in ids.items()} if isinstance(ids, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def mark_seen(ai_id: str, proposal_id: str, *, home: Path | None = None) -> None:
    """Record that this practice has read ``proposal_id``; keeps the newest MAX_IDS. Never raises."""
    if not (ai_id and proposal_id):
        return
    path = _path(ai_id, home)
    ids = _read(path)
    ids[proposal_id] = time.time()
    if len(ids) > MAX_IDS:
        ids = dict(sorted(ids.items(), key=lambda kv: kv[1])[-MAX_IDS:])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"ids": ids}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def seen_ids(ai_id: str, *, home: Path | None = None) -> set[str]:
    """The ids this practice has read on this machine."""
    return set(_read(_path(ai_id, home))) if ai_id else set()

"""Ownership of pending request files.

A pending file is named ``<kind>_pending_<instance>_<name>.json``. Instance ids and names both contain underscores, so the glob
for ``seat`` also matches ``seat_2``'s files and a consumer that deletes what it read destroyed another instance's requests. The
payload carries the instance the file was written FOR; this is the shared filter on it.

A file whose payload cannot say (unreadable, missing the key, not a string) keeps the glob match: it is still reported and removed
by the consumer rather than stranded for ever.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path


def payload_instance(path: Path, normalise: Callable[[str], str]) -> str | None:
    """The instance a pending file was written for (normalised the way filenames are), or None when it cannot say."""
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("instance_id")
    except (OSError, ValueError, AttributeError):
        return None
    return normalise(value) if isinstance(value, str) else None


def owned_by(paths: list[Path], safe_instance: str, normalise: Callable[[str], str]) -> list[Path]:
    """The glob matches that belong to ``safe_instance`` (plus the ones whose payload cannot say)."""
    return [p for p in paths if payload_instance(p, normalise) in (None, safe_instance)]

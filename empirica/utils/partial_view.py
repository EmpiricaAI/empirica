"""Record when a CLI response was a partial page, where a hook can read it.

A paged response carries its own completeness (`has_more`, `matched`,
`total_matching`, `truncated`), but the reader rarely sees it: measured over this
box's transcripts, 2,061 calls piped a paged empirica or gh command through jq,
which keeps the rows and drops the fields that said they were a page. A notice on
stderr does not survive either: 835 of those calls fed stderr into jq (a notice
would break the parse) and 1,243 discarded it.

So the CLI writes one small file per instance when it returns a partial page,
and the PostToolUse truncation hook consumes it after the Bash call and adds a
line to what the model reads. The file is keyed by the same instance suffix the
Sentinel uses, and the hook deletes it on read, so one partial page is reported
once.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

FILENAME = "partial_view{suffix}.json"


def path_for(suffix: str) -> Path:
    return Path.home() / ".empirica" / FILENAME.format(suffix=suffix)


def _as_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def completeness_gap(result: object) -> dict | None:
    """The response's own statement that it is partial, or None if it makes none.

    Reads only what the response declares. A response that declares nothing is
    not thereby complete — it simply gives this check nothing to go on.
    """
    if not isinstance(result, dict):
        return None
    returned = _as_int(result.get("returned"))
    if returned is None:
        returned = _as_int(result.get("count"))
    if returned is None:
        returned = _as_int(result.get("goals_count"))
    total = _as_int(result.get("matched"))
    if total is None:
        total = _as_int(result.get("total_matching"))

    declared = result.get("has_more") is True or result.get("truncated") is True
    short = total is not None and returned is not None and total > returned
    if not (declared or short):
        return None
    gap: dict = {}
    if returned is not None:
        gap["returned"] = returned
    if total is not None:
        gap["matched"] = total
    for key in ("has_more", "truncated", "truncated_hint", "limit"):
        if key in result and result[key] not in (None, False):
            gap[key] = result[key]
    return gap


def record(verb: str, result: dict) -> bool:
    """Write the partial-page record for this instance. True if one was written.

    Best effort by design: a failed write leaves the hook with nothing to report,
    which it already says it cannot distinguish from a complete view.
    """
    gap = completeness_gap(result)
    if gap is None:
        return False
    try:
        from empirica.utils.session_resolver import _get_instance_suffix

        target = path_for(_get_instance_suffix())
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"verb": verb, "ts": time.time(), **gap}))
        os.replace(tmp, target)
        return True
    except Exception:
        return False

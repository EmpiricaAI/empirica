#!/usr/bin/env python3
"""PostToolUse(Bash): say so when the output the model just read was partial.

Truncation is the failure where an incomplete view reads as a complete one, and
the reader reasons from what is missing as though it were absent. Two kinds can
be detected deterministically from the command and its output
(prop_mysjzbnpvjezfb2s6eokgnnn6q, empirica-outreach; ECO-accepted by David):

1. Self-inflicted. The command's own last stage asked for N lines (`head -N`,
   `head -n N`, `sed -n a,bp`) and got exactly N. Asked 40 and got 12 means the
   whole output was seen; asked 40 and got 40 means it almost certainly went on.
   Only single-command lines are judged: in `a; b | head -5` the output of `a`
   is mixed in and the line count proves nothing. `tail` is not flagged — a
   tail that fills its window is nearly always a deliberate test summary.
2. Declared. The response said it was a page (`has_more`, `truncated`,
   `matched` above what was returned). Either visible in stdout, or recorded
   by the empirica CLI in a per-instance file that survives `| jq`, which keeps
   the rows and drops the fields (empirica/utils/partial_view.py).

Measured before building, over 132,208 Bash calls in this box's transcripts:
class 1 fires on about 2% of calls, 1,688 of them on searches and --help pages;
2,061 paged commands went through jq.

What this cannot see, and every notice says so: rows a filter excluded, a
search run over the wrong population, a sample that is all there is. Silence
from this hook does not mean the view was complete.

The notice goes in additionalContext, the channel measured to reach the model;
it never blocks and never alters the output. Stdlib only, except the instance
resolver, which is imported only for commands that invoke empirica.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

REACH = (
    "Checked: limits in your own command and pages the response declared. "
    "Not checkable here: rows a filter excluded, or a search over the wrong population."
)

_SPLIT = re.compile(r";|&&|\|\||\n")
_HEAD = re.compile(r"^head\s+(?:-n\s*|-)(\d+)\s*$")
_HEAD_BARE = re.compile(r"^head\s*$")
_SED = re.compile(r"""^sed\s+-n\s+['"]?(\d+),(\d+)p['"]?\s*$""")
_DECLARED = [
    ("has_more", re.compile(r'"has_more"\s*:\s*true')),
    ("truncated", re.compile(r'"truncated"\s*:\s*true')),
]
_PAIRS = [
    ("matched", re.compile(r'"matched"\s*:\s*(\d+)'), re.compile(r'"(?:returned|count)"\s*:\s*(\d+)')),
    ("total_matching", re.compile(r'"total_matching"\s*:\s*(\d+)'), re.compile(r'"(?:goals_count|count)"\s*:\s*(\d+)')),
]
MAX_RECORD_AGE_S = 600


def self_limit(command: str) -> tuple[str, int] | None:
    """(stage, N) when a single-command line ends in a prefix limit, else None."""
    cmd = command.strip()
    if not cmd or _SPLIT.search(cmd):
        return None
    stage = cmd.split("|")[-1].strip()
    if "|" not in cmd:
        return None  # `head -5 file` reads a file whose length can be checked directly
    m = _HEAD.match(stage)
    if m:
        return stage, int(m.group(1))
    if _HEAD_BARE.match(stage):
        return stage, 10
    m = _SED.match(stage)
    if m:
        return stage, int(m.group(2)) - int(m.group(1)) + 1
    return None


def count_lines(text: str) -> int:
    return len(text.rstrip("\n").splitlines()) if text.strip() else 0


def declared_in_output(stdout: str) -> list[str]:
    found = [name for name, rx in _DECLARED if rx.search(stdout)]
    for name, big, small in _PAIRS:
        b, s = big.search(stdout), small.search(stdout)
        if b and s and int(b.group(1)) > int(s.group(1)):
            found.append(f"{name} {b.group(1)} > returned {s.group(1)}")
    return found


def consume_record(command: str) -> dict | None:
    """The CLI's partial-page record for this instance, removed on read."""
    if "empirica" not in command:
        return None
    try:
        from empirica.utils.session_resolver import _get_instance_suffix

        path = Path.home() / ".empirica" / f"partial_view{_get_instance_suffix()}.json"
        if not path.exists():
            return None
        record = json.loads(path.read_text())
        os.unlink(path)
    except Exception:
        return None
    if not isinstance(record, dict) or time.time() - float(record.get("ts", 0)) > MAX_RECORD_AGE_S:
        return None
    return record


def _page_text(record: dict) -> str:
    verb = record.get("verb") or "the empirica command"
    returned, matched = record.get("returned"), record.get("matched")
    size = f"{returned} of {matched}" if returned is not None and matched is not None else "a partial page"
    hint = f" {record['truncated_hint']}." if record.get("truncated_hint") else ""
    return f"`{verb}` returned {size}: the response declared itself a page, whatever reached you after filtering.{hint}"


def notices(command: str, stdout: str) -> list[str]:
    out: list[str] = []
    limit = self_limit(command)
    if limit and count_lines(stdout) == limit[1]:
        stage, n = limit
        out.append(
            f"`{stage}` returned exactly {n} lines, so the output almost certainly continued past what "
            "you saw. Before concluding something is absent, re-run with a larger limit or without it."
        )
    record = consume_record(command)
    if record:
        out.append(_page_text(record))
    else:
        found = declared_in_output(stdout)
        if found:
            out.append(f"The response declared itself partial ({', '.join(found)}): you hold a page, not the answer.")
    return out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    response = payload.get("tool_response")
    stdout = response.get("stdout", "") if isinstance(response, dict) else str(response or "")
    found = notices(command, stdout if isinstance(stdout, str) else "")
    if not found:
        return 0
    text = "Truncation: " + " ".join(found) + f" ({REACH})"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())

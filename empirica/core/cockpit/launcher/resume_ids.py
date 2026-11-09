"""The Claude Code session a cockpit pane was running, so `refresh` can resume that conversation by id (goal dfa8b484).

`claude --continue` resumes the newest conversation in a directory, which is another conversation when two panes share a practice or a
headless run happened there since. Each cockpit pane carries an instance id (EMPIRICA_INSTANCE_ID), and the session hooks record its current
session in ``~/.empirica/instance_projects/<instance id>.json``. The id is trusted only when that record is for the pane's own project and a
transcript with that id exists and was started in that directory; otherwise the caller falls back to ``--continue``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_SESSION_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_HEAD_BYTES = 64 * 1024  # the first records of a transcript carry its cwd


def _same_dir(a: str, b: str) -> bool:
    return os.path.realpath(os.path.expanduser(a)) == os.path.realpath(os.path.expanduser(b))


def _transcript_cwd(path: Path) -> str | None:
    """The working directory recorded in a transcript's first records, or None when none is found."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(_HEAD_BYTES)
    except OSError:
        return None
    for raw in head.split(b"\n"):
        if b'"cwd"' not in raw:
            continue
        try:
            record = json.loads(raw)
        except ValueError:
            continue
        cwd = record.get("cwd") if isinstance(record, dict) else None
        if isinstance(cwd, str) and cwd:
            return cwd
    return None


def last_session_id(instance_id: str | None, project_path: str) -> str | None:
    """The session id this pane last ran in ``project_path``, or None when it cannot be confirmed."""
    if not instance_id or "/" in instance_id or instance_id.startswith("."):
        return None
    home = Path.home()
    try:
        data = json.loads(
            (home / ".empirica" / "instance_projects" / f"{instance_id}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sid, recorded = data.get("claude_session_id"), data.get("project_path")
    if not isinstance(sid, str) or not _SESSION_ID.match(sid) or not isinstance(recorded, str):
        return None
    if not _same_dir(recorded, project_path):
        return None
    for transcript in (home / ".claude" / "projects").glob(f"*/{sid}.jsonl"):
        cwd = _transcript_cwd(transcript)
        if cwd and _same_dir(cwd, project_path):
            return sid
    return None

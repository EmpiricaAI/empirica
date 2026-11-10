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


# ── codex panes: ecodex's own record, keyed by pane (prop_yv6d62yl5jgkbkayjmb47wel3e) ─────────────────────────────────────
#
# Codex panes get no cockpit-bound identity, so ecodex's SessionStart writes ~/.empirica/pane_sessions/<pane number>.json:
# {harness: "codex", session_id, project_path, started, tmux_server_start}. tmux reuses pane numbers after a server restart,
# so the record is trusted only for the same harness, the pane's own project, and the live server's start time.

_CODEX_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")


def codex_session_id(pane_id: str | None, project_path: str, tmux_server_start: int | str | None) -> str | None:
    """The codex session this pane last ran, or None when the record cannot be trusted."""
    if not pane_id or not re.fullmatch(r"%\d{1,6}", pane_id) or tmux_server_start in (None, ""):
        return None
    try:
        data = json.loads(
            (Path.home() / ".empirica" / "pane_sessions" / f"{pane_id[1:]}.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("harness") != "codex":
        return None
    sid, recorded = data.get("session_id"), data.get("project_path")
    if not isinstance(sid, str) or not _CODEX_ID.match(sid) or not isinstance(recorded, str):
        return None
    if str(data.get("tmux_server_start")) != str(tmux_server_start) or not _same_dir(recorded, project_path):
        return None
    return sid

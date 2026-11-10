"""Whether a cockpit pane is waiting on its person: a permission dialog or a question (goal dfa8b484). Stdlib only.

Claude Code has hooks for a dialog being shown (PermissionRequest; AskUserQuestion's PreToolUse) and none for it being answered, so the
flag is set on the first and cleared by whatever the pane does next: a tool finishing or failing, a prompt, the turn ending. Setting it puts a
pane option the cockpit bar draws (`@empirica_waiting`) and a small file; the hooks that clear it test only for the file, so a pane that is not
waiting pays one stat and never starts tmux. Nothing here decides anything or can fail a hook: every error is swallowed.

Loaded by file path from the hooks beside it, never through the empirica package, which costs an import on every call.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

_PANE = re.compile(r"^%(\d{1,6})$")
OPTION = "@empirica_waiting"
LABEL_MAX = 60


def _flag_dir() -> Path:
    return Path.home() / ".empirica" / "pane_waiting"


def current_pane() -> str | None:
    """This process's tmux pane (`%12`), from the environment Claude Code passes its hooks; None outside tmux."""
    pane = os.environ.get("TMUX_PANE", "")
    return pane if _PANE.match(pane) else None


def flag_path(pane: str) -> Path:
    return _flag_dir() / pane.lstrip("%")


def _tmux(*args: str) -> bool:
    try:
        return subprocess.run(["tmux", *args], capture_output=True, timeout=2, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def label_for(tool_name: str | None) -> str:
    """What the bar shows: `question` for a question, `approve <tool>` for a permission dialog."""
    name = str(tool_name or "").strip()
    if name == "AskUserQuestion":
        return "question"
    name = re.sub(r"[^A-Za-z0-9_.:-]", "", name)[:40]  # a tool name only: nothing a tmux format would read as a format
    return f"approve {name}" if name else "approve"


_TOOL_EVENTS = ("PostToolUse", "PostToolUseFailure")


def owner_of(payload: dict | None) -> dict | None:
    """Which tool call a dialog belongs to: its ``tool_use_id`` when the hook carries one, and always the tool name and a
    digest of its input, which PermissionRequest and PostToolUse both carry. None when the payload names no tool."""
    if not isinstance(payload, dict) or not payload.get("tool_name"):
        return None
    import hashlib

    body = json.dumps(payload.get("tool_input") or {}, sort_keys=True, default=str)
    owner = {"tool": str(payload["tool_name"]), "input": hashlib.sha256(body.encode()).hexdigest()[:16]}
    if payload.get("tool_use_id"):
        owner["tool_use_id"] = str(payload["tool_use_id"])
    return owner


def _same_call(owner: dict, payload: dict) -> bool:
    other = owner_of(payload) or {}
    if owner.get("tool_use_id") and other.get("tool_use_id"):
        return owner["tool_use_id"] == other["tool_use_id"]
    return owner.get("tool") == other.get("tool") and owner.get("input") == other.get("input")


def set_waiting(pane: str | None, label: str, owner: dict | None = None) -> bool:
    """Mark ``pane`` waiting with ``label``, for the tool call ``owner`` names. True when the pane option was set."""
    if not pane or not _PANE.match(pane):
        return False
    label = re.sub(r"[#{}]", "", label)[:LABEL_MAX]
    record: dict = {"label": label, "ts": time.time()}
    if owner:
        record["owner"] = owner
    try:
        _flag_dir().mkdir(parents=True, exist_ok=True)
        flag_path(pane).write_text(json.dumps(record), encoding="utf-8")
    except OSError:
        return False
    return _tmux("set-option", "-p", "-t", pane, OPTION, label)


def clear_if_waiting(pane: str | None, payload: dict | None = None) -> bool:
    """Clear ``pane``'s waiting mark. One stat when it is not set; True when a mark was cleared.

    A tool finishing or failing clears only the dialog its own call opened: with parallel calls, another call finishing
    while the dialog is still open used to clear it early. A prompt, the turn ending, or no payload clears whatever is set,
    and so does a mark recorded without an owner (written before owners were kept, or for a question).
    """
    if not pane or not _PANE.match(pane):
        return False
    flag = flag_path(pane)
    if not flag.exists():
        return False
    if isinstance(payload, dict) and payload.get("hook_event_name") in _TOOL_EVENTS:
        try:
            owner = json.loads(flag.read_text(encoding="utf-8")).get("owner")
        except (OSError, ValueError, AttributeError):
            owner = None
        if isinstance(owner, dict) and not _same_call(owner, payload):
            return False
    try:
        flag.unlink()
    except OSError:
        pass
    _tmux("set-option", "-p", "-u", "-t", pane, OPTION)
    return True


def waiting_label(pane: str | None) -> str | None:
    if not pane or not _PANE.match(pane):
        return None
    try:
        data = json.loads(flag_path(pane).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    label = data.get("label") if isinstance(data, dict) else None
    return label if isinstance(label, str) and label else None


def notify(title: str, body: str) -> bool:
    """A desktop notification through notify-send, where it exists. Detached; True when one was started."""
    exe = shutil.which("notify-send")
    if not exe:
        return False
    try:
        subprocess.Popen(
            [exe, "--app-name=Empirica", title[:120], body[:240]],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return False
    return True

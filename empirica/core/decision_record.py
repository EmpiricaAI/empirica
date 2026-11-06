"""One appended JSON line per Sentinel PreToolUse decision, in the practice's ``.empirica/decisions.jsonl``.

What it is for: the labels a local decision model learns from (ecodex, prop_35xy4qkfgvcvph6rqpbzpbueey) and the audit trail of
the discipline layer planned for 1.15. Each line says what the gate decided, which stage decided it, the mode and vocabulary the
decision was made in, and what the call was: the tool, the program a shell command runs, and a SHA-256 of the input. The input
itself is never written, because a command or a file body can carry a secret.

What it does not do:

* It is not tamper-proof. The agent whose calls are recorded can edit or delete the file with its own shell. It is a record for
  measurement, not evidence against the agent.
* ``effect_class`` (what the call changed) and ``human_override`` (a person approving or denying at the prompt) are null here:
  PreToolUse runs before the call and before any prompt, so neither is visible to it. They belong to the PostToolUse side.
* ``rule`` names the stage of the gate that decided (``noetic-firewall``, ``pipeline``, ...), not the individual check inside it.

Cost: one ``open(..., "a")`` and one write per call, no database, no network. ``EMPIRICA_DECISION_RECORD=0`` turns it off.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import time
from pathlib import Path

# Bumped when a field's meaning or a value's vocabulary changes, so a reader can tell records made under different rules apart.
VOCABULARY_VERSION = "1"

# The gate as it behaves today: everything that is not a recognised read needs a transaction. The 1.15 discipline layer adds
# other modes; until then every record is made in this one.
MODE = "strict"

MAX_BYTES = 10 * 1024 * 1024
FILE_NAME = "decisions.jsonl"

_OFF = {"0", "false", "no", "off"}
_WRAPPERS = {"sudo", "env", "nice", "nohup", "time", "command", "exec"}


def enabled() -> bool:
    return (os.environ.get("EMPIRICA_DECISION_RECORD") or "").strip().lower() not in _OFF


def input_hash(tool_input: object) -> str:
    """SHA-256 of the input as canonical JSON: equal inputs give equal hashes, and the input cannot be read back from it."""
    body = json.dumps(tool_input, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()


def command_class(tool_name: str, tool_input: object) -> str:
    """The program a shell command runs (``git`` for ``FOO=1 /usr/bin/git push``), or the tool's name for any other tool."""
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return tool_name
    command = str(tool_input.get("command") or "")
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    for word in words:
        if "=" in word and not word.startswith(("/", ".")) and word.split("=", 1)[0].isidentifier():
            continue  # an environment assignment in front of the program
        name = os.path.basename(word)
        if name in _WRAPPERS:
            continue
        return name or tool_name
    return tool_name


def build(
    *,
    tool_name: str,
    tool_input: object,
    decision: str,
    rule: str,
    tool_use_id: str | None = None,
    claude_session_id: str | None = None,
    transaction_id: str | None = None,
    tx_status: str | None = None,
    harness: str | None = None,
) -> dict:
    return {
        "ts": round(time.time(), 3),
        "vocabulary": VOCABULARY_VERSION,
        "mode": MODE,
        "harness": harness,
        "claude_session_id": claude_session_id,
        "transaction_id": transaction_id,
        "tx_status": tx_status,
        "tool_use_id": tool_use_id,
        "tool": tool_name,
        "command_class": command_class(tool_name, tool_input),
        "input_sha256": input_hash(tool_input),
        "decision": decision,
        "rule": rule,
        "effect_class": None,
        "human_override": None,
    }


def record_path(project_path: str | None) -> Path:
    """The practice's file when the call is in a practice, else the seat's (``~/.empirica``), so no decision goes unrecorded."""
    if project_path:
        return Path(project_path) / ".empirica" / FILE_NAME
    return Path.home() / ".empirica" / FILE_NAME


def append(record: dict, path: Path, *, max_bytes: int = MAX_BYTES) -> bool:
    """Append one line. True when it was written; False when it could not be, never an exception on the tool path.

    A file at or over ``max_bytes`` is moved to ``decisions.jsonl.1`` first (one backup, replaced each time), so the record is
    bounded at about twice the cap.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if path.stat().st_size >= max_bytes:
                os.replace(path, path.with_name(path.name + ".1"))
        except FileNotFoundError:
            pass
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False

"""One appended JSON line per Sentinel PreToolUse decision, in the practice's ``.empirica/decisions.jsonl``.

What it is for: the labels a local decision model learns from (ecodex, prop_35xy4qkfgvcvph6rqpbzpbueey) and the audit trail of
the discipline layer planned for 1.15. Each line says what the gate decided, which stage decided it, the mode and vocabulary the
decision was made in, and what the call was: the tool, the program a shell command runs, and a SHA-256 of the input. The input
itself is never written, because a command or a file body can carry a secret.

What it does not do:

* It is not tamper-proof. The agent whose calls are recorded can edit or delete the file with its own shell. It is a record for
  measurement, not evidence against the agent.
* ``effect_classes`` (what the call changed) and ``human_override`` (a person approving or denying at the prompt) are null here:
  PreToolUse runs before the call and before any prompt, so neither is visible to it. They belong to the PostToolUse side.
* ``permission_mode`` is the harness's own, as its hook input carries it (null when it does not): the same call carries a different risk under
  a read-only mode than under one that lets it write.
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
    permission_mode: str | None = None,
) -> dict:
    return {
        "ts": round(time.time(), 3),
        "vocabulary": VOCABULARY_VERSION,
        "mode": MODE,
        "harness": harness,
        "permission_mode": permission_mode,
        "claude_session_id": claude_session_id,
        "transaction_id": transaction_id,
        "tx_status": tx_status,
        "tool_use_id": tool_use_id,
        "tool": tool_name,
        "command_class": command_class(tool_name, tool_input),
        "input_sha256": input_hash(tool_input),
        "decision": decision,
        "rule": rule,
        "effect_classes": None,
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


# ── the outcome half: one "post" line per call, paired with its decision on tool_use_id ─────────────────────────────────

# The hooks that write it, each carrying the same loader (tests/test_decision_record.py keeps the copies identical).
OUTCOME_HOOKS = ("entity-extractor.py", "truncation-legibility.py", "tool-failure.py")

# What a call changed. Draft, checked by ecodex (prop_2zqpvjdjwfbghgbi6f2rjtghgm). Only the four path classes are produced so
# far, for Edit, Write and codex's apply_patch; a shell call is recorded with no class and observed false until the before-and-after comparison exists.
EFFECT_CLASSES = (
    "none",
    "project_write",
    "outside_write",
    "config_write",
    "empirica_state",
    "vcs_local",
    "vcs_remote",
    "network_send",
    "install",
    "process",
    "delete_recursive",
    "credential_write",
    "remote_exec",
    "external_tool",
    "unknown",
)

_CONFIG_DIRS = (".claude", ".codex", ".config")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def path_effect(file_path: str, project_path: str | None) -> str:
    """The effect class of writing ``file_path``: the practice's own state and the seat's are ``empirica_state``, harness and
    shell configuration is ``config_write``, anything else in the practice is ``project_write``, the rest ``outside_write``."""
    path = Path(os.path.abspath(os.path.expanduser(file_path)))
    home = Path(os.path.abspath(os.path.expanduser("~")))
    project = Path(os.path.abspath(project_path)) if project_path else None
    if _inside(path, home / ".empirica") or (project is not None and _inside(path, project / ".empirica")):
        return "empirica_state"
    if any(_inside(path, home / d) for d in _CONFIG_DIRS) or (path.parent == home and path.name.startswith(".")):
        return "config_write"
    if project is not None and _inside(path, project):
        return "project_write"
    return "outside_write"


def project_path_for(claude_session_id: str | None, cwd: str | None) -> str | None:
    """The practice a call belongs to: the session's active work, else the working directory when it is a practice root."""
    if claude_session_id:
        try:
            data = json.loads((Path.home() / ".empirica" / f"active_work_{claude_session_id}.json").read_text())
            if data.get("project_path"):
                return str(data["project_path"])
        except (OSError, ValueError, AttributeError):
            pass
    if cwd and (Path(cwd) / ".empirica" / "project.yaml").is_file():
        return str(cwd)
    return None


_PATCH_HEADERS = ("*** Add File: ", "*** Delete File: ", "*** Update File: ", "*** Move to: ")


def patch_paths(patch: str) -> list[str]:
    """Every path a codex ``apply_patch`` names, in order: added, deleted, updated, and the destination of a move.

    The grammar is codex's own (codex-rs/apply-patch/src/parser.rs, as ecodex cited it): file headers between
    ``*** Begin Patch`` and ``*** End Patch``. Text that does not begin a patch names nothing.
    """
    lines = (patch or "").splitlines()
    if not lines or lines[0].strip() != "*** Begin Patch":
        return []
    paths: list[str] = []
    for line in lines[1:]:
        if line.strip() == "*** End Patch":
            break
        for header in _PATCH_HEADERS:
            if line.startswith(header):
                path = line[len(header) :].strip()
                if path:
                    paths.append(path)
                break
    return paths


def _written_paths(tool_name: str, tool_input: object, cwd: str | None) -> list[str]:
    """The files an Edit, Write or apply_patch call wrote to, absolute. A patch's relative paths are read against ``cwd``."""
    if not isinstance(tool_input, dict):
        return []
    if tool_name in ("Edit", "Write"):
        return [str(tool_input["file_path"])] if tool_input.get("file_path") else []
    if tool_name == "apply_patch":
        base = cwd or os.getcwd()
        return [
            p if os.path.isabs(p) else os.path.join(base, p) for p in patch_paths(str(tool_input.get("command") or ""))
        ]
    return []


def build_outcome(hook_input: dict, outcome: str, project_path: str | None) -> dict:
    """The post line. ``effect_classes`` is the sorted set of classes of the places a successful file edit wrote; null for a
    shell command (not observed yet), for any other tool, and for a call that did not succeed."""
    tool_name = str(hook_input.get("tool_name") or "unknown")
    tool_input = hook_input.get("tool_input") or {}
    effects = None
    if outcome == "success":
        paths = _written_paths(tool_name, tool_input, hook_input.get("cwd"))
        if paths:
            effects = sorted({path_effect(path, project_path) for path in paths})
    return {
        "ts": round(time.time(), 3),
        "vocabulary": VOCABULARY_VERSION,
        "event": "post",
        "harness": (os.environ.get("EMPIRICA_HARNESS") or "claude-code").strip().lower() or "claude-code",
        "claude_session_id": hook_input.get("session_id"),
        "tool_use_id": hook_input.get("tool_use_id"),
        "tool": tool_name,
        "outcome": outcome,
        "effect_classes": effects,
        "observed": effects is not None,
    }


def record_outcome(hook_input: dict, outcome: str) -> bool:
    """Append the post line for this call. ``outcome`` is success, failure or interrupted. Never raises."""
    try:
        if not enabled() or not isinstance(hook_input, dict):
            return False
        project = project_path_for(hook_input.get("session_id"), hook_input.get("cwd"))
        return append(build_outcome(hook_input, outcome, project), record_path(project))
    except Exception:
        return False

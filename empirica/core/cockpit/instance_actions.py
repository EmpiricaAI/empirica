"""Instance lifecycle actions — kill, forget, label.

These are the *destructive* control plane: they end an instance's life
or scrub its state. The non-destructive controls (sentinel pause/resume,
loop pause/resume) live in the sibling modules.

Design notes:

- `kill`: tmux instances get `tmux kill-pane -t %{N}`. Non-tmux instances
  fall back to SIGTERM via tracked PID (read from
  ~/.empirica/tty_sessions/{tty_key}.json which session-init populates),
  with `--force` upgrading to SIGKILL. If we have neither tmux pane nor
  PID, we cannot reach the instance — return a clear error.

- `forget`: removes every per-instance state file under ~/.empirica/.
  Idempotent. Safe to run on already-dead instances. Does NOT touch the
  project's `.empirica/active_transaction*.json` — that lives in the
  project tree and is the project's record, not the instance's.

- `label`: writes ~/.empirica/instance_label_{id}. Empty string clears.

All operations are file-level + signal-level only — no DB writes. They
are inspectable via `ls ~/.empirica/`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

EMPIRICA_DIR = Path.home() / ".empirica"
TTY_SESSIONS_DIR = EMPIRICA_DIR / "tty_sessions"

TMUX_INSTANCE_PATTERN = re.compile(r"^tmux_(.+)$")


def _safe_suffix(text: str) -> str:
    return text.replace("/", "-").replace("%", "")


def label_file_path(instance_id: str) -> Path:
    return EMPIRICA_DIR / f"instance_label_{_safe_suffix(instance_id)}"


def set_label(instance_id: str, label: str | None) -> str | None:
    """Write or clear the instance label. Returns the new label, or None if cleared."""
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = label_file_path(instance_id)
    if not label:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return None
    path.write_text(label.strip() + "\n", encoding="utf-8")
    return label.strip()


def get_label(instance_id: str) -> str | None:
    path = label_file_path(instance_id)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
        return text.splitlines()[0].strip() if text else None
    except OSError:
        return None


def _get_pid_from_tty(instance_id: str) -> int | None:
    """Find a PID we can signal to terminate the instance.

    session-init records both pid (the hook process — short-lived, usually
    dead by query time) and ppid (the Claude Code parent — the long-lived
    process we actually want to signal). We prefer ppid, falling back to pid
    if ppid is missing or already dead.

    Resolution order:
      1. instance_projects/{id}.json ppid → pid
      2. tty_sessions/{tty_key}.json   ppid → pid
    """

    def _pick_alive(data: dict) -> int | None:
        for key in ("ppid", "pid"):
            value = data.get(key)
            if isinstance(value, int) and value > 1 and _process_alive(value):
                return value
        # Even if not alive, return ppid as a hint for the caller.
        for key in ("ppid", "pid"):
            value = data.get(key)
            if isinstance(value, int) and value > 1:
                return value
        return None

    inst_file = EMPIRICA_DIR / "instance_projects" / f"{instance_id}.json"
    if inst_file.exists():
        try:
            with open(inst_file, encoding="utf-8") as f:
                inst = json.load(f)
            pid = _pick_alive(inst)
            if pid:
                return pid
            tty_key = inst.get("tty_key")
        except (OSError, json.JSONDecodeError):
            tty_key = None
    else:
        tty_key = None

    if not tty_key:
        return None

    tty_file = TTY_SESSIONS_DIR / f"{tty_key}.json"
    if not tty_file.exists():
        return None
    try:
        with open(tty_file, encoding="utf-8") as f:
            tty = json.load(f)
        return _pick_alive(tty)
    except (OSError, json.JSONDecodeError):
        return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False


@dataclass
class KillResult:
    instance_id: str
    method: str  # 'tmux' | 'sigterm' | 'sigkill' | 'unreachable'
    success: bool
    detail: str
    pid: int | None = None


@dataclass
class StopResult:
    """Result of a `stop` action — soft interrupt, recoverable."""

    instance_id: str
    success: bool
    detail: str
    method: str  # 'tmux-send-keys' | 'unreachable'


@dataclass
class WakeResult:
    """Result of a `wake` action — nudge an idle AI to submit a turn so
    UserPromptSubmit-stage hooks fire and surface queued pending state
    (install requests, listener arms, etc.)."""

    instance_id: str
    success: bool
    detail: str
    method: str  # 'tmux-send-keys' | 'unreachable'


def wake_instance(instance_id: str) -> WakeResult:
    """Nudge an idle Claude Code session to submit a turn — the remote-Enter.

    Sends a Space + Enter to the target pane via `tmux send-keys`. That
    submits a one-character prompt which triggers UserPromptSubmit hooks
    (loop-install-pickup, listener-install-pickup, ewm-protocol-loader,
    tool-router) so any queued pending state gets surfaced as a
    system-reminder to the AI on the very next turn — no waiting for the
    user to type something.

    Used by the TUI Events button so pressing Events ON for a fresh
    instance actively triggers loop/listener install on the target AI
    rather than just writing a pending file and hoping the user prompts
    next. Closes the gap between "I clicked it" and "the AI is doing it."

    For non-tmux instances we have no shell-agnostic way to inject a
    keystroke — return unreachable. The pending file is still written
    and will fire on the user's next manual prompt (graceful degrade).
    """
    if not instance_id:
        raise ValueError("instance_id required")

    m = TMUX_INSTANCE_PATTERN.match(instance_id)
    if not m:
        return WakeResult(
            instance_id=instance_id,
            success=False,
            method="unreachable",
            detail="non-tmux instance — pending request will fire on next user prompt",
        )

    pane_n = m.group(1)
    if shutil.which("tmux") is None:
        return WakeResult(
            instance_id=instance_id,
            success=False,
            method="unreachable",
            detail="tmux binary not found in PATH",
        )

    try:
        result = subprocess.run(
            ["tmux", "send-keys", "-t", f"%{pane_n}", " ", "Enter"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        return WakeResult(
            instance_id=instance_id,
            success=False,
            method="tmux-send-keys",
            detail="tmux send-keys timed out",
        )

    if result.returncode == 0:
        return WakeResult(
            instance_id=instance_id,
            success=True,
            method="tmux-send-keys",
            detail=f"sent Space+Enter to pane %{pane_n}",
        )
    stderr = (result.stderr or "").strip() or "<no stderr>"
    return WakeResult(
        instance_id=instance_id,
        success=False,
        method="tmux-send-keys",
        detail=f"tmux send-keys returned {result.returncode}: {stderr}",
    )


def stop_instance(instance_id: str, key: str = "Escape") -> StopResult:
    """Send a soft interrupt to a running Claude — the remote-spacebar.

    For tmux instances: `tmux send-keys -t %N <key>`. The default Escape
    matches Claude Code's interrupt key (stops generation without killing
    the process). The Claude instance survives — only the current turn is
    interrupted.

    For non-tmux instances we have no shell-agnostic way to inject a
    keystroke into the TTY; return unreachable. This is recoverable: the
    user can interrupt manually in that terminal.
    """
    if not instance_id:
        raise ValueError("instance_id required")

    m = TMUX_INSTANCE_PATTERN.match(instance_id)
    if not m:
        return StopResult(
            instance_id=instance_id,
            success=False,
            method="unreachable",
            detail="non-tmux instance — interrupt manually in that terminal",
        )

    pane_n = m.group(1)
    if shutil.which("tmux") is None:
        return StopResult(
            instance_id=instance_id,
            success=False,
            method="unreachable",
            detail="tmux binary not found in PATH",
        )

    try:
        result = subprocess.run(
            ["tmux", "send-keys", "-t", f"%{pane_n}", key],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except subprocess.TimeoutExpired:
        return StopResult(
            instance_id=instance_id,
            success=False,
            method="tmux-send-keys",
            detail="tmux send-keys timed out",
        )

    if result.returncode == 0:
        return StopResult(
            instance_id=instance_id,
            success=True,
            method="tmux-send-keys",
            detail=f"sent {key} to pane %{pane_n}",
        )
    stderr = (result.stderr or "").strip() or "<no stderr>"
    return StopResult(
        instance_id=instance_id,
        success=False,
        method="tmux-send-keys",
        detail=f"tmux send-keys returned {result.returncode}: {stderr}",
    )


def _kill_via_tmux(instance_id: str, pane_n: str) -> KillResult:
    if shutil.which("tmux") is None:
        return KillResult(instance_id, "tmux", False, "tmux binary not found in PATH")
    try:
        result = subprocess.run(
            ["tmux", "kill-pane", "-t", f"%{pane_n}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired:
        return KillResult(instance_id, "tmux", False, "tmux kill-pane timed out after 5s")

    if result.returncode == 0:
        return KillResult(instance_id, "tmux", True, f"tmux pane %{pane_n} killed")
    stderr = (result.stderr or "").strip() or "<no stderr>"
    return KillResult(instance_id, "tmux", False, f"tmux kill-pane returned {result.returncode}: {stderr}")


def _kill_via_signal(instance_id: str, force: bool) -> KillResult:
    pid = _get_pid_from_tty(instance_id)
    if pid is None:
        return KillResult(
            instance_id,
            "unreachable",
            False,
            "no tracked PID for this instance — kill the terminal manually, then `empirica instance forget`",
            pid=None,
        )

    if not _process_alive(pid):
        return KillResult(instance_id, "sigterm", True, f"process {pid} already dead", pid=pid)

    sig = signal.SIGKILL if force else signal.SIGTERM
    method = "sigkill" if force else "sigterm"
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        return KillResult(instance_id, method, True, f"process {pid} disappeared", pid=pid)
    except PermissionError as e:
        return KillResult(instance_id, method, False, f"permission denied killing pid {pid}: {e}", pid=pid)

    # SIGTERM is not always immediate — give the process a moment.
    if not force:
        for _ in range(10):
            time.sleep(0.1)
            if not _process_alive(pid):
                return KillResult(instance_id, "sigterm", True, f"pid {pid} terminated", pid=pid)
        return KillResult(
            instance_id,
            "sigterm",
            True,
            f"sent SIGTERM to {pid} (still alive after 1s — use --force for SIGKILL)",
            pid=pid,
        )

    return KillResult(instance_id, "sigkill", True, f"sent SIGKILL to {pid}", pid=pid)


def kill_instance(instance_id: str, force: bool = False) -> KillResult:
    """Terminate an instance. tmux pane for tmux_*, signal for others."""
    if not instance_id:
        raise ValueError("instance_id required")

    m = TMUX_INSTANCE_PATTERN.match(instance_id)
    if m:
        return _kill_via_tmux(instance_id, m.group(1))

    return _kill_via_signal(instance_id, force=force)


# ─── forget ────────────────────────────────────────────────────────────────

# File patterns that hold per-instance state. The braces are filled with
# instance_id (sanitized). Both .json and bare-flag forms.
_FORGET_PATTERNS = (
    "instance_projects/{id}.json",
    "sentinel_paused_{id}",
    "loops_{id}.json",
    "active_session_{id}",
    "hook_counters_{id}.json",
    "context_usage_{id}.json",
    "cortex_remote_cache_{id}.json",
    "pre_tx_calls_{id}.json",
    "instance_label_{id}",
)

_LOOP_PAUSE_PATTERN = "loop_paused_{id}_*"


@dataclass
class ForgetResult:
    instance_id: str
    removed: list[str]
    skipped: list[str]


def forget_instance(instance_id: str) -> ForgetResult:
    """Remove every per-instance state file from ~/.empirica/.

    Idempotent. Project-tree state (.empirica/active_transaction_*.json) is
    NOT touched — that's the project's record, not the instance's.
    """
    if not instance_id:
        raise ValueError("instance_id required")
    safe_id = _safe_suffix(instance_id)
    removed: list[str] = []
    skipped: list[str] = []

    for pattern in _FORGET_PATTERNS:
        path = EMPIRICA_DIR / pattern.format(id=safe_id)
        if path.exists():
            try:
                path.unlink()
                removed.append(str(path.relative_to(EMPIRICA_DIR)))
            except OSError as e:
                skipped.append(f"{path.relative_to(EMPIRICA_DIR)}: {e}")

    # Loop pause sidecars carry an extra _{loop_name} suffix — glob them.
    for path in EMPIRICA_DIR.glob(_LOOP_PAUSE_PATTERN.format(id=safe_id)):
        try:
            path.unlink()
            removed.append(str(path.relative_to(EMPIRICA_DIR)))
        except OSError as e:
            skipped.append(f"{path.relative_to(EMPIRICA_DIR)}: {e}")

    return ForgetResult(instance_id=instance_id, removed=removed, skipped=skipped)


@dataclass
class RebindResult:
    instance_id: str
    success: bool
    pid: int | None
    detail: str


def rebind_instance(instance_id: str) -> RebindResult:
    """Re-stamp an instance's captured pid/ppid + start time from its live
    process, matched by ``EMPIRICA_INSTANCE_ID``.

    Lets a resumed or manually-restarted instance refresh its record without
    running a transaction (the only other refresh path) — and unlike
    ``forget``/``prune`` it re-registers rather than deletes. No-op failure
    when there is no record to update or no live claude process declares this
    instance_id.
    """
    if not instance_id:
        raise ValueError("instance_id required")
    from empirica.core.cockpit.liveness import live_claude_pids_by_instance

    inst_file = EMPIRICA_DIR / "instance_projects" / f"{_safe_suffix(instance_id)}.json"
    if not inst_file.exists():
        return RebindResult(instance_id, False, None, "no instance record found")

    scan = live_claude_pids_by_instance()
    if scan is None:
        return RebindResult(instance_id, False, None, "process scan unavailable (psutil missing)")
    match = scan.get(instance_id)
    if not match:
        return RebindResult(instance_id, False, None, "no live claude process declares this instance_id")

    pid, ct = match
    try:
        with open(inst_file, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return RebindResult(instance_id, False, None, f"could not read record: {e}")

    # The live claude process IS the durable parent for the liveness signal:
    # store it as both pid and ppid (target_pid = ppid) with its start time so
    # the create_time guard matches.
    data["pid"] = pid
    data["ppid"] = pid
    data["ppid_create_time"] = ct
    data["rebound_at"] = time.time()
    try:
        with open(inst_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.chmod(inst_file, 0o600)
    except OSError as e:
        return RebindResult(instance_id, False, pid, f"could not write record: {e}")

    return RebindResult(instance_id, True, pid, f"rebound to live pid {pid}")


__all__ = [
    "ForgetResult",
    "KillResult",
    "RebindResult",
    "StopResult",
    "forget_instance",
    "get_label",
    "kill_instance",
    "rebind_instance",
    "set_label",
    "stop_instance",
]

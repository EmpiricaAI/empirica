"""Cached deploy-gap verdict for SessionStart.

`empirica doctor --deploy-gaps` answers "what is committed but not live on this
box" — CLI vs checkout, deployed plugin vs package, MCP wrapper vs core,
unreleased commits. The detector existed for weeks and nothing ran it, which is
how a whole mesh diagnosed a NULL column for an hour against code that was
correct in every checkout and executing in none.

It costs about 2.1 s per run (measured 2.18 / 2.02 / 2.27), which rules out a
synchronous call on the SessionStart hot path. So session start READS a small
cached verdict and, when the cache is missing, stale or old, spawns a detached
refresh that writes the next one. The verdict always states its age and whether
the box has moved since it was measured, because a cache is itself a
two-states hazard: a fresh clean verdict and a stale clean verdict must not
render alike.

Layout: one JSON file per project root under ``~/.empirica/deploy_gaps/``.
Written atomically (tmp + rename). A refresh that fails writes an error
verdict rather than nothing, so "never measured" and "measurement broke" stay
distinguishable at the next session start.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

#: A verdict older than this is refreshed even when the fingerprint still
#: matches — the plugin or pipx copy can change without moving anything the
#: fingerprint watches (a re-vendor that keeps the version stamp, for one).
MAX_AGE_SECONDS = 24 * 3600

#: A refresh lock younger than this means one is already running; do not
#: stack another. Long enough for a slow box, short enough that a crashed
#: refresher does not block the next session for the rest of the day.
LOCK_TTL_SECONDS = 600

REFRESH_TIMEOUT_SECONDS = 90


def _home() -> Path:
    return Path.home()


def cache_dir(home: Path | None = None) -> Path:
    return (home or _home()) / ".empirica" / "deploy_gaps"


def cache_path(project_root: Path, home: Path | None = None) -> Path:
    key = hashlib.sha1(str(Path(project_root).resolve()).encode("utf-8")).hexdigest()[:12]
    return cache_dir(home) / f"{key}.json"


# ─── Fingerprint: what would change the verdict ──────────────────────────


def _head_sha(project_root: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=str(project_root),
            check=False,
        )
    except Exception:
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _file_stamp(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        st = path.stat()
    except OSError:
        return None
    return f"{path}:{st.st_mtime_ns}:{st.st_size}"


def fingerprint(project_root: Path, home: Path | None = None, path_env: str | None = None) -> dict[str, str | None]:
    """The inputs whose change would change the verdict. Cheap: one git
    rev-parse and two stats. None where an input is absent; absence is itself
    part of the fingerprint (a binary appearing is a change)."""
    cli = shutil.which("empirica", path=path_env) if path_env is not None else shutil.which("empirica")
    cli_path = Path(cli).resolve() if cli else None
    plugin_stamp = (home or _home()) / ".claude" / "plugins" / "local" / "empirica" / ".plugin-version"
    return {
        "head": _head_sha(project_root),
        "cli": _file_stamp(cli_path),
        "plugin": _file_stamp(plugin_stamp),
    }


def fingerprint_delta(cached: dict[str, Any] | None, now: dict[str, Any]) -> list[str]:
    """Which watched inputs moved since the verdict was measured. Empty means
    the verdict still describes this box."""
    if not isinstance(cached, dict):
        return sorted(now.keys())
    return sorted(k for k in now if cached.get(k) != now.get(k))


# ─── Read / write ────────────────────────────────────────────────────────


def read_verdict(project_root: Path, home: Path | None = None) -> dict[str, Any] | None:
    p = cache_path(project_root, home)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "measured_at" in data else None


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    os.replace(tmp, path)


def verdict_from_doctor(doctor_json: dict[str, Any], fp: dict[str, Any], measured_at: float) -> dict[str, Any]:
    """Keep what session start needs and nothing that would let the cache grow
    into a second doctor: the summary counts, and every check that is not a
    PASS with its detail and hint. SKIP is kept but is not a gap."""
    checks = doctor_json.get("checks") or []
    attention = [
        {
            "name": c.get("name"),
            "status": c.get("status"),
            "detail": c.get("detail") or "",
            "hint": c.get("hint") or "",
        }
        for c in checks
        if isinstance(c, dict) and c.get("status") != "PASS"
    ]
    return {
        "measured_at": measured_at,
        "fingerprint": fp,
        "summary": doctor_json.get("summary") or {},
        "attention": attention,
        "error": None,
    }


def error_verdict(reason: str, fp: dict[str, Any], measured_at: float) -> dict[str, Any]:
    return {"measured_at": measured_at, "fingerprint": fp, "summary": {}, "attention": [], "error": reason}


def refresh(
    project_root: Path,
    home: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """Run the detector and write the verdict. Runs in the detached child, never
    on the hot path. A failed run writes an error verdict: the next session
    start then says the measurement broke, instead of reading the absence as
    "never measured" forever."""
    fp = fingerprint(project_root, home)
    measured_at = clock()
    try:
        r = runner(
            ["empirica", "doctor", "--deploy-gaps", "--output", "json"],
            capture_output=True,
            text=True,
            timeout=REFRESH_TIMEOUT_SECONDS,
            cwd=str(project_root),
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except Exception as e:  # timeout, missing binary, anything
        verdict = error_verdict(f"{type(e).__name__}: {e}"[:200], fp, measured_at)
    else:
        if r.returncode != 0 or not (r.stdout or "").strip():
            tail = (r.stderr or "").strip().splitlines()[-1:] or ["no output"]
            verdict = error_verdict(f"doctor exited {r.returncode}: {tail[0][:160]}", fp, measured_at)
        else:
            try:
                verdict = verdict_from_doctor(json.loads(r.stdout), fp, measured_at)
            except ValueError as e:
                verdict = error_verdict(f"doctor output was not JSON: {e}"[:200], fp, measured_at)
    _write_atomic(cache_path(project_root, home), verdict)
    return verdict


# ─── Detached refresh ────────────────────────────────────────────────────


def _lock_path(project_root: Path, home: Path | None) -> Path:
    return cache_path(project_root, home).with_suffix(".lock")


def spawn_refresh(
    project_root: Path,
    home: Path | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    clock: Callable[[], float] = time.time,
) -> bool:
    """Start one detached refresher for this project. Returns True when one was
    started, False when a live lock says one is already running or the spawn
    failed. The child is this module run as a script."""
    lock = _lock_path(project_root, home)
    try:
        age = clock() - lock.stat().st_mtime
        if age < LOCK_TTL_SECONDS:
            return False
    except OSError:
        pass
    try:
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(str(os.getpid()), encoding="utf-8")
        os.utime(lock, None)
        popen(
            [sys.executable, str(Path(__file__).resolve()), "--refresh", str(project_root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except Exception:
        return False
    return True


# ─── Render ──────────────────────────────────────────────────────────────


def _age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


_MOVED = {"head": "the checkout moved", "cli": "the empirica binary changed", "plugin": "the deployed plugin changed"}

DOCTOR_CMD = "`empirica doctor --deploy-gaps`"


def render(
    verdict: dict[str, Any] | None,
    moved: list[str],
    now: float,
    refreshing: bool,
) -> str:
    """The SessionStart text. Empty only for a fresh, clean verdict — every
    other state (unmeasured, failed, stale, gaps) says so in words."""
    tail = " Refreshing in the background." if refreshing else ""
    if verdict is None:
        return f"Deploy gaps: not yet measured on this box.{tail} {DOCTOR_CMD} shows it now."
    age = _age(now - float(verdict.get("measured_at") or 0))
    if verdict.get("error"):
        return (
            f"Deploy gaps: last measurement {age} ago FAILED ({verdict['error']}).{tail} Run {DOCTOR_CMD} to see why."
        )
    gaps = [a for a in verdict.get("attention") or [] if a.get("status") in ("WARN", "FAIL")]
    moved_text = ", ".join(_MOVED.get(m, m) for m in moved)
    if not gaps:
        if moved:
            return f"Deploy gaps: clean verdict from {age} ago predates this box ({moved_text}).{tail}"
        return ""
    head = f"## ⚠️ Deploy gaps on this box — measured {age} ago"
    if moved:
        head += f" (before {moved_text}; may have changed{'; refreshing' if refreshing else ''})"
    lines = [head, ""]
    for g in gaps:
        line = f"- **{g.get('status')}** {g.get('name')}: {g.get('detail')}"
        if g.get("hint"):
            line += f" → {g['hint']}"
        lines.append(line)
    lines.append("")
    lines.append(f"Code you fixed may not be the code that runs. {DOCTOR_CMD} re-measures now.")
    return "\n".join(lines)


def session_start_block(
    project_root: Path,
    home: Path | None = None,
    spawner: Callable[[Path, Path | None], bool] = spawn_refresh,
    clock: Callable[[], float] = time.time,
) -> str:
    """Hot-path entry: read, compare, maybe spawn, render. No detector run."""
    project_root = Path(project_root)
    verdict = read_verdict(project_root, home)
    now = clock()
    fp_now = fingerprint(project_root, home)
    moved = fingerprint_delta(verdict.get("fingerprint") if verdict else None, fp_now)
    too_old = verdict is not None and (now - float(verdict.get("measured_at") or 0)) > MAX_AGE_SECONDS
    needs_refresh = verdict is None or bool(verdict.get("error")) or bool(moved) or too_old
    refreshing = spawner(project_root, home) if needs_refresh else False
    return render(verdict, moved if verdict else [], now, refreshing)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) == 2 and argv[0] == "--refresh":
        root = Path(argv[1])
        try:
            refresh(root)
        finally:
            try:
                _lock_path(root, None).unlink()
            except OSError:
                pass
        return 0
    sys.stderr.write("usage: deploy_gap_cache.py --refresh <project_root>\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Empirica PostToolUseFailure Hook — Auto-log dead-ends from tool failures.

Fires when a tool call fails. Logs the failure as a dead-end to prevent
re-exploration of failed approaches. Tracks failure patterns.

Input: tool_name, tool_input, error, is_interrupt
Can block: No (tool already failed)
"""

import json
import logging
import re
import subprocess
import sys
from pathlib import Path

LOG_DIR = Path.home() / ".empirica" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("empirica.tool-failure")
handler = logging.FileHandler(LOG_DIR / "tool-failure.log")
handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.DEBUG)

# Tool failures that are noise (not worth logging as dead-ends)
IGNORE_TOOLS = {
    "Read",  # File not found is normal exploration
    "Glob",  # No matches is normal
    "Grep",  # No matches is normal
    "LSP",  # LSP failures are common and transient
}

# Error patterns that are noise
# A dead-end is an EPISTEMIC judgment — "this approach does not work" — and it is
# retrieved into later sessions as "avoid re-trying". An operational hiccup is not
# that. Conflating them floods the graph with noise that then steers future work:
# measured 2026-07-27, 637 of 750 open dead-ends on this practice were captured tool
# failures, including a `git commit` that SUCCEEDED (its own why_failed text contains
# the successful push) and was recorded because a CI-wait loop in the same command
# hit `timeout`. Future sessions were being told to avoid re-trying `git commit`.
IGNORE_PATTERNS = [
    "No such file or directory",
    "No matches found",
    "not a tty",
    "Permission denied",  # Usually sandbox, not a real dead-end
    # Timeouts / signals — the process was killed by the clock or the harness, which
    # says nothing about whether the APPROACH is viable. This was the dominant miss:
    # a timeout message is long, so the >=20-char heuristic waved it straight through.
    "Command timed out",
    "Exit code 143",  # SIGTERM (timeout)
    "Exit code 137",  # SIGKILL (OOM / hard kill)
    "timed out after",
    "Timeout",
    "moved to the background",
    # Non-zero exits that are ordinary, informative results rather than failures.
    "Exit code 1\n",  # bare grep/rg "no match" — a finding, not a dead end
    "No such container",
    "Connection refused",  # a service being down is operational, not epistemic
    "Could not resolve host",
    "Temporary failure in name resolution",
]

# Output shapes that indicate the command actually DID its work before something else in
# the same invocation failed. A partial success must never be recorded as an approach
# that does not work.
#
# Each is the SHAPE the success takes, not a word from it: as bare substrings, "->" vetoed every Rust compile error
# (`--> src/main.rs:2:5`) and any traceback quoting an annotated signature, "passed" vetoed "too many arguments passed",
# and "Successfully" vetoed pip's "Successfully built x ... Failed to build y" (goal 546e7c33).
SUCCESS_MARKERS = [
    re.compile(
        r"^\s*\+?\s*[0-9a-f]{7,}\.\.\.?[0-9a-f]{7,}\s+\S+ -> \S+", re.M
    ),  # git push: old..new or + old...new (forced)
    re.compile(r"^\s*[*+] \[new (?:branch|tag)\]\s+\S+ -> \S+", re.M),  # git push of a new branch or tag
    re.compile(r"\b\d+ files? changed\b"),  # git commit / diff --stat
    re.compile(
        r"\bSuccessfully (?:installed|uninstalled|tagged|created|pushed|deployed|published|updated|merged|rebased)\b"
    ),
    re.compile(r"\b\d+ passed\b"),  # pytest's summary line
    re.compile("\u2705"),
]

# The CLI reports its own success as JSON. A result that says `"ok": true` is work that landed whatever the process exit
# code was (a later command in the same line failed), and recording it taught later sessions to avoid a working verb
# (prop_bxjntibm defect 5). A nested `"ok": false` anywhere in the same output keeps it a failure.
_OK_TRUE = re.compile(r'"ok"\s*:\s*true')
_OK_FALSE = re.compile(r'"ok"\s*:\s*false')


def _is_interesting_failure(tool_name: str, error: str) -> bool:
    """Is this failure worth recording as a permanent epistemic dead-end?

    The bar is deliberately high. A dead-end is retrieved into later sessions as
    "avoid re-trying", so a false positive does not merely add noise — it removes a
    viable approach from the practice's option space, and until migration 060 nothing
    could ever contradict it.
    """
    if tool_name in IGNORE_TOOLS:
        return False
    if any(pattern in error for pattern in IGNORE_PATTERNS):
        return False
    # If the output shows the work landed, the command did not fail in the sense that
    # matters — something later in the same invocation did.
    if any(marker.search(error) for marker in SUCCESS_MARKERS):
        return False
    if _OK_TRUE.search(error) and not _OK_FALSE.search(error):
        return False
    # Short errors are usually transient
    return len(error) >= 20


def _truncate(s: str, max_len: int = 200) -> str:
    """Truncate string for logging."""
    return s[:max_len] + "..." if len(s) > max_len else s


# ── Credential redaction ──────────────────────────────────────────────
#
# DELIBERATE DUPLICATE of empirica/core/redaction.py. Hooks run standalone —
# they cannot import from the package — so the logic is copied rather than
# shared. `tests/test_secret_redaction.py` pins BOTH implementations against
# one corpus so the copy cannot drift silently.
#
# Why this exists: `str(tool_input)` below stringifies the whole tool payload,
# and MCP `cortex_*` tools take `api_key` as a parameter — so a live credential
# was being written into `dead_end.approach` and retrieved into later sessions.
# Redaction happens BEFORE truncation: truncating first can slice a token in
# half and leave a prefix that no pattern then matches.
_SECRET_KEY_NAMES = [
    "api_key",
    "apikey",
    "key",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "session_key",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "pwd",
    "authorization",
    "auth",
    "credential",
    "credentials",
    "private_key",
    "signing_key",
]

_SHAPE_PATTERNS = (
    (re.compile(r"\bctx_[A-Za-z0-9_\-]{8,}"), "ctx_<redacted>"),
    (re.compile(r"\btk_[A-Za-z0-9]{20,}"), "tk_<redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "sk-<redacted>"),
    (re.compile(r"\bghp_[A-Za-z0-9]{16,}"), "ghp_<redacted>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "github_pat_<redacted>"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "xox-<redacted>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA<redacted>"),
    (re.compile(r"\bmst-[A-Za-z0-9]{16,}"), "mst-<redacted>"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]+"), "<redacted>-jwt"),
    # Generalised over the auth SCHEME — matching `bearer` only was a scheme
    # allowlist, and `Authorization: token <hex>` (forgejo/gitea) walked through in
    # clear. Matching the header NAME means Basic / ApiKey / the next scheme need no
    # new rule. Scheme preserved; credential destroyed.
    (
        re.compile(
            r"(?i)\b(authorization|proxy-authorization)(\s*:\s*)([A-Za-z][A-Za-z0-9_-]*[ \t]+)?[A-Za-z0-9._\-+/=]{8,}"
        ),
        r"\1\2\3<redacted>",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer <redacted>"),
    (re.compile(r"(?i)\btoken\s+[A-Za-z0-9._\-]{12,}"), "token <redacted>"),
    # `https://user:token@host` — how a git remote carries a forgejo token, so it
    # lands in any captured git remote/push command. Scheme + user survive.
    (re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.\-]*://)([^/\s:@]+):([^/\s@]{4,})@"), r"\1\2:<redacted>@"),
)
_KV_PATTERN = re.compile(
    r"(?i)(['\"]?\b(?:" + "|".join(sorted(_SECRET_KEY_NAMES)) + r")\b['\"]?\s*[:=]\s*)(['\"])([^'\"]{4,}?)\2"
)
_FLAG_PATTERN = re.compile(
    r"(?i)(--(?:" + "|".join(sorted(n.replace("_", "-") for n in _SECRET_KEY_NAMES)) + r")[= ])(\S{4,})"
)


def _redact(text):
    """Scrub secret-shaped tokens from free text. Fails CLOSED — if the redactor
    itself errors we emit the placeholder, never the raw text."""
    if not text:
        return text
    try:
        out = str(text)
        out = _KV_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>{m.group(2)}", out)
        out = _FLAG_PATTERN.sub(lambda m: f"{m.group(1)}<redacted>", out)
        for pattern, replacement in _SHAPE_PATTERNS:
            out = pattern.sub(replacement, out)
        return out
    except Exception:
        return "<redacted>"


def _is_secret_key(name):
    n = str(name).strip().lower().lstrip("-").replace("-", "_")
    return n in _SECRET_KEY_NAMES or any(n.endswith("_" + k) for k in _SECRET_KEY_NAMES)


def _scrub_mapping(data, _depth=0):
    """Drop secret-NAMED values before the payload is stringified. Exact where
    the shape patterns are heuristic — `{"password": "hunter2"}` has no
    recognizable shape, only a recognizable key."""
    if _depth > 6:
        return data
    if isinstance(data, dict):
        return {k: ("<redacted>" if _is_secret_key(k) else _scrub_mapping(v, _depth + 1)) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_scrub_mapping(v, _depth + 1) for v in data]
    if isinstance(data, str):
        return _redact(data)
    return data


def _decision_record():
    """empirica/core/decision_record.py, loaded from its file. Importing it through the package runs empirica/core's own
    imports (about 22 ms measured), and this runs after every call. None when empirica is not installed. The same function is
    copied into each hook that records an outcome; tests/test_decision_record.py keeps the copies identical."""
    try:
        import importlib.util

        spec = importlib.util.find_spec("empirica")
        if spec is None or not spec.submodule_search_locations:
            return None
        path = Path(next(iter(spec.submodule_search_locations))) / "core" / "decision_record.py"
        module_spec = importlib.util.spec_from_file_location("_empirica_decision_record", path)
        if module_spec is None or module_spec.loader is None:
            return None
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        return module
    except Exception:
        return None


def _record_outcome(hook_input, outcome: str) -> None:
    record = _decision_record()
    if record is not None:
        record.record_outcome(hook_input, outcome)


def _clear_pane_waiting() -> None:
    """Clear this cockpit pane's waiting mark, if one is set (pane_waiting.py beside this hook). One stat when none is.

    Claude Code has no hook for a permission dialog or a question being answered, so whatever the pane does next clears it. The
    same function is copied into each hook that clears; tests/test_cockpit_pane_waiting.py keeps the copies identical."""
    import os
    from pathlib import Path

    pane = os.environ.get("TMUX_PANE", "")
    if not pane.startswith("%"):
        return
    try:
        import zlib  # not hashlib: 1.8 ms to import on every hook call, against 0.05 ms

        sock = os.environ.get("TMUX", "").split(",")[0]  # per tmux server: pane numbers repeat across servers
        name = f"{zlib.crc32(sock.encode()):08x}_{pane[1:]}" if sock else pane[1:]
        if not (Path.home() / ".empirica" / "pane_waiting" / name).exists():  # inside the try: a stat can raise
            return
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_empirica_pane_waiting", Path(__file__).with_name("pane_waiting.py")
        )
        if spec is not None and spec.loader is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.clear_if_waiting(pane)
    except Exception:
        pass


def main():
    _clear_pane_waiting()
    try:
        hook_input = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        hook_input = {}

    tool_name = hook_input.get("tool_name", "unknown")
    tool_input = hook_input.get("tool_input", {})
    error = hook_input.get("error", "")
    is_interrupt = hook_input.get("is_interrupt", False)
    _record_outcome(hook_input, "interrupted" if is_interrupt else "failure")

    logger.info(f"ToolFailure: {tool_name} | interrupt={is_interrupt} | {_truncate(error, 100)}")

    # Skip interrupts — user cancelled, not a real failure
    if is_interrupt:
        logger.debug("  Skipping interrupt")
        sys.exit(0)

    # Skip uninteresting failures
    if not _is_interesting_failure(tool_name, error):
        logger.debug(f"  Skipping noise failure for {tool_name}")
        sys.exit(0)

    # Build a meaningful description of what failed
    # Redact BEFORE truncating — truncation can split a token and leave a prefix
    # that no pattern matches afterwards.
    if tool_name == "Bash":
        command = tool_input.get("command", "unknown command")
        approach = f"Bash: {_truncate(_redact(command), 150)}"
    elif tool_name in ("Edit", "Write"):
        file_path = tool_input.get("file_path", "unknown file")
        approach = f"{tool_name}: {file_path}"
    else:
        # `str(tool_input)` is where MCP `api_key` parameters leaked in. Scrub the
        # mapping by key name first, then regex-scrub the rendered string.
        approach = f"{tool_name}: {_truncate(_redact(str(_scrub_mapping(tool_input))), 150)}"

    why_failed = _truncate(_redact(error), 300)

    # Log as dead-end
    try:
        result = subprocess.run(
            ["empirica", "deadend-log", "--approach", approach, "--why-failed", why_failed],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            logger.info(f"  Logged dead-end: {approach}")
        else:
            logger.warning(f"  Failed to log dead-end: {result.stderr}")
    except Exception as e:
        logger.warning(f"  Exception logging dead-end: {e}")

    sys.exit(0)


if __name__ == "__main__":
    main()

"""Which model and reasoning-effort tier each seat is running.

None of the structured surfaces carries this: `empirica status --all`,
`practice-context`, and the cortex roster all describe the practice, not the
model driving it. The only place it appears is the seat's own Claude Code
transcript, where assistant records carry a nested `message.model` and, when
the harness sets one, a top-level `effort`.

**Reading a transcript is opt-in and off by default.** The flag lives on the
practice whose log would be opened (`cockpit.read_transcripts` in its
`project.yaml`), so the subject grants the access rather than the operator
granting it on the subject's behalf. With the flag unset this module opens
nothing and returns ``(None, None)``, which the caller renders as "not
observed".

Design notes worth keeping:

* **An unobserved value is never guessed.** Every failure path returns
  ``(None, None)`` and the caller renders a placeholder. An invented model
  name would be indistinguishable from an observed one, which is the failure
  mode this whole column exists to avoid.
* **Only the tail is read.** Transcripts reach tens of megabytes and the
  values we want are the most recent, so only the last 256KB is scanned,
  backwards. A torn first line from slicing mid-file is skipped by the
  ``json.loads`` guard rather than repaired.
* **Cached on (mtime, size)** of the newest transcript — re-reading every
  seat on every refresh would be absurd for a 1s-refresh TUI.
* **The format is not ours.** The path layout and record shape belong to
  Claude Code and can change without notice. When they do, this degrades to
  "not observed" rather than breaking the cockpit — and on any other harness
  there is simply no such file, so the columns stay empty.

Adapted into core from a tenant-local implementation on Philipp's box,
carried by empirica-mesh-support (prop_ycat73pyavc5vaj2mxsfpm7s2q). Rewritten
here rather than applied as a foreign commit, and gated per David's ruling.
"""

from __future__ import annotations

import glob
import json
import os

# Claude Code's transcript root. Vendor-private layout — see module docstring.
_PROJECTS = os.path.expanduser("~/.claude/projects")

# How much of the transcript tail to read. 256KB comfortably spans the last
# several exchanges even with large tool results.
_TAIL_BYTES = 256 * 1024

# transcript path -> (mtime, size, model, effort)
_CACHE: dict[str, tuple[float, int, str | None, str | None]] = {}


def _transcript_dir(project_path: str) -> str:
    """Claude Code mangles a project path into a flat directory name."""
    return os.path.join(_PROJECTS, project_path.replace("/", "-"))


def _newest_transcript(project_path: str) -> str | None:
    try:
        files = glob.glob(os.path.join(_transcript_dir(project_path), "*.jsonl"))
        if not files:
            return None
        return max(files, key=os.path.getmtime)
    except OSError:
        return None


def _parse_tail(path: str) -> tuple[str | None, str | None]:
    """Scan the transcript tail backwards for the newest model and effort."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            start = max(0, fh.tell() - _TAIL_BYTES)
            fh.seek(start)
            chunk = fh.read()
    except OSError:
        return (None, None)

    model: str | None = None
    effort: str | None = None
    for raw in reversed(chunk.split(b"\n")):
        if model and effort:
            break
        # Cheap reject before paying for a JSON parse — most records carry
        # neither field.
        if b'"model"' not in raw and b'"effort"' not in raw:
            continue
        try:
            rec = json.loads(raw)
        except ValueError:
            # Expected on the first line when the tail slice cut mid-record.
            continue
        if not isinstance(rec, dict):
            continue
        if not model:
            msg = rec.get("message")
            if isinstance(msg, dict):
                found = msg.get("model")
                model = found if isinstance(found, str) else None
        if not effort:
            found = rec.get("effort")
            effort = found if isinstance(found, str) else None
    return (model, effort)


def read_model_effort(project_path: str | None) -> tuple[str | None, str | None]:
    """Return ``(model, effort)`` for a seat, or ``(None, None)``.

    ``(None, None)`` covers every not-observed case and they are deliberately
    indistinguishable to the caller: transcript reading not enabled for this
    practice, no transcript, unreadable file, or fields absent. The caller
    renders a placeholder either way, so no branch can leak a guess.
    """
    if not project_path:
        return (None, None)

    # Consent gate first: with the flag off we must not even stat the path.
    from empirica.core.cockpit.project_cockpit_config import transcript_reading_enabled

    if not transcript_reading_enabled(project_path):
        return (None, None)

    path = _newest_transcript(project_path)
    if not path:
        return (None, None)

    try:
        st = os.stat(path)
    except OSError:
        return (None, None)

    cached = _CACHE.get(path)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return (cached[2], cached[3])

    model, effort = _parse_tail(path)
    _CACHE[path] = (st.st_mtime, st.st_size, model, effort)
    return (model, effort)


def short_model(model: str | None, width: int = 10) -> str | None:
    """``'claude-opus-4-8'`` -> ``'opus-4-8'``.

    Strips the vendor prefix only — the version-bearing tail is left intact,
    because `opus-5` vs `opus-4-8` is exactly the distinction the column
    exists to show. ``None`` stays ``None``; how an unobserved value is
    rendered is the caller's decision, not this function's.
    """
    if not model:
        return None
    trimmed = model.removeprefix("claude-")
    return trimmed[:width]


__all__ = ["read_model_effort", "short_model"]

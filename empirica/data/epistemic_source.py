"""Epistemic source tagging for artifacts (source-aware Sentinel substrate).

Per docs/architecture/PROMPT_FOR_EMPIRICA_CLAUDE_source_aware_sentinel.md.

Every finding/decision/unknown/mistake/assumption/dead-end can be tagged
with how the AI arrived at it:

- ``intuition``  — generated from training data + already-loaded session
  context, no external retrieval since the goal opened.
- ``search``     — produced or substantially shaped by an external retrieval
  this session (file read, grep, glob, web fetch, MCP tool call,
  project_search, etc.).
- ``mixed``      — both intuition and search contributed.
- ``None``       — legacy / not yet tagged. Default for back-compat.

The Sentinel uses per-goal source ratios as a calibration signal: vectors
asserted high ``know`` while every artifact is ``intuition``-tagged is
exactly the gaming surface ecodex's brief identifies.

This is v0 — the data primitive only. The routing rule (gate route to
"investigate" when claims are high but evidence is all-intuition) is
deferred until calibration history accumulates.
"""

from typing import Literal

EpistemicSource = Literal['intuition', 'search', 'mixed']

EPISTEMIC_SOURCES: tuple[str, ...] = ('intuition', 'search', 'mixed')


def normalize_epistemic_source(value: str | None) -> str | None:
    """Return a valid source tag, or ``None`` for unknown/missing values.

    Unlike visibility (which falls back to ``shared``), ``None`` is the
    legitimate default here — it means "not tagged". Coercing unknown
    values to a tag would silently misclassify intuition as search or
    vice versa, polluting the calibration signal.
    """
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in EPISTEMIC_SOURCES:
        return v
    return None

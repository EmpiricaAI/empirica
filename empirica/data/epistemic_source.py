"""Epistemic source tagging for artifacts (source-aware Sentinel substrate).

Per docs/architecture/PROMPT_FOR_EMPIRICA_CLAUDE_source_aware_sentinel.md (not in this repo).

Every finding/decision/unknown/mistake/assumption/dead-end can be tagged
with how the AI arrived at it:

- ``intuition``  — generated from training data + already-loaded session
  context, no external retrieval since the goal opened.
- ``search``     — produced or substantially shaped by an external retrieval
  this session (file read, grep, glob, web fetch, MCP tool call,
  project_search, etc.).
- ``mixed``      — both intuition and search contributed.
- ``harvested``  — back-filled by /graph-harvest from material that already existed (a document, a conversation),
  not observed in the transaction that logged it. Stored with no transaction id, so calibration never counts it
  (``graded_transaction``).
- ``None``       — legacy / not yet tagged. Default for back-compat.

The Sentinel uses per-goal source ratios as a calibration signal: vectors
asserted high ``know`` while every artifact is ``intuition``-tagged is
exactly the gaming surface ecodex's brief identifies.

This is v0 — the data primitive only. The routing rule (gate route to
"investigate" when claims are high but evidence is all-intuition) is
deferred until calibration history accumulates.
"""

from typing import Literal

EpistemicSource = Literal["intuition", "search", "mixed", "harvested"]

EPISTEMIC_SOURCES: tuple[str, ...] = ("intuition", "search", "mixed", "harvested")


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


def graded_transaction(epistemic_source: str | None, transaction_id: str | None) -> str | None:
    """The transaction an artifact is stored under: none for a harvested one.

    Every artifact count calibration grades is scoped to the graded transaction or its session, and the writers take
    the transaction from whatever one is open. A harvested artifact was not observed in that transaction, so it is
    stored outside every graded window here, at the one place all writers share, rather than filtered out of each
    evidence query (goal 3e9cb368).
    """
    return None if normalize_epistemic_source(epistemic_source) == "harvested" else transaction_id

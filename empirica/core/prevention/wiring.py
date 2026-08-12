"""PREFLIGHT emission — the half of the prevention pipeline nothing called.

`apply_prevention_detection` has run at every POSTFLIGHT since P1-v1 landed,
over an emitter with zero call sites: modules existed, rows never did (the
existence-vs-function retraction, 2026-08-12). This module is the missing
producer: when PREFLIGHT surfaces retrieved anti-patterns into the AI's
context, each surfaced item becomes one `exposed` prevention_event, which the
POSTFLIGHT oracle can then advance to `prevented` or `failed`.

v1 trigger semantics (proposed to ecodex for EXP-SHADOW co-design, and
deliberately simple):

- **What counts as an exposure**: an item surfaced in one of the three
  anti-pattern classes — `dead_ends`, `prior_mistakes`, `lessons`. Findings /
  eidetic facts are knowledge, not warnings, and are not exposures.
- **acknowledged=True at emission**: injection into the PREFLIGHT response IS
  delivery into context — the strongest deliverable form short of a reply-back
  protocol, which does not exist. This is the explicitly-revisable co-design
  point: if EXP-SHADOW needs delivery≠read distinguished, this flag moves.
- **Subject scope**: PREFLIGHT usually runs before any goal exists, so rows
  are emitted with goal_id NULL and the oracle matches failures
  session-scoped for NULL-goal rows (see detection.py). Session ≈ subject is
  honest at PREFLIGHT time; per-goal attribution starts when emission points
  gain a goal (future refinement, not silently pretended now).
"""

from __future__ import annotations

import hashlib
import logging

from .persist import emit_prevention_exposure

logger = logging.getLogger(__name__)

# The classes whose surfacing constitutes an anti-pattern exposure.
EXPOSURE_CLASSES = ("dead_ends", "prior_mistakes", "lessons")


def _pattern_key(cls: str, item: dict) -> str:
    """Stable identity for a surfaced pattern: real id when the payload has
    one, else a content hash — never positional, so re-surfacing the same
    pattern tomorrow dedupes against today's exposure."""
    ident = item.get("id") or item.get("item_id") or item.get("lesson_id")
    if not ident:
        basis = str(item.get("content") or item.get("approach") or item.get("mistake") or item.get("name") or item)
        ident = hashlib.sha256(basis.encode()).hexdigest()[:16]
    return f"{cls}:{ident}"


def emit_preflight_exposures(db, session_id: str, transaction_id: str | None, patterns: dict | None) -> int:
    """Emit one exposure row per surfaced anti-pattern. Returns rows written.

    Fail-open and duplicate-safe: a pattern already exposed in this session
    (any outcome) is not re-emitted — re-surfacing is retrieval doing its job,
    not a new treatment event. Errors never propagate into PREFLIGHT.
    """
    if not patterns or not isinstance(patterns, dict):
        return 0
    try:
        written = 0
        for cls in EXPOSURE_CLASSES:
            items = patterns.get(cls)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = _pattern_key(cls, item)
                exists = db.conn.execute(
                    "SELECT 1 FROM prevention_events WHERE session_id = ? AND pattern_key = ? LIMIT 1",
                    (session_id, key),
                ).fetchone()
                if exists:
                    continue
                emit_prevention_exposure(
                    db,
                    session_id,
                    transaction_id,
                    pattern_key=key,
                    subject_key=f"session:{session_id}",
                    acknowledged=True,  # injected into context — see module docstring
                )
                written += 1
        if written:
            logger.debug(f"prevention: {written} exposure(s) emitted at PREFLIGHT")
        return written
    except Exception as e:
        logger.debug(f"prevention emission failed (non-fatal): {e}")
        return 0

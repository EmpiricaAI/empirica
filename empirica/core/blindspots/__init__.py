"""Blindspot detection — inferring unknown-unknowns from the artifact corpus.

A blindspot is a **predicted-necessary assessment that is absent, unacknowledged,
and inferred from the artifacts themselves** — the negative space of what the
practice has logged. Unlike an ``unknown`` (a known-unknown: an acknowledged gap),
a blindspot is an UNacknowledged gap the corpus predicts should be filled.

Candidate generation is deliberately NOT keyword/ontology based (brittle, fires on
vocabulary coincidence). It is inferred from the corpus, cheapest-and-least-noisy
signal first:

1. **intent-gap** (this module) — a stated goal/task with no covering artifact and
   no acknowledging unknown. Measured against the practice's own declared intent,
   so it is the highest-confidence signal. Ships first, dry-run.
2. co-occurrence — what work shaped like this usually assessed that this hasn't (T5).
3. fossils — where mistakes/dead-ends historically materialized (T6).

See ``docs`` / the blindspot goal for the full transaction plan.
"""

from __future__ import annotations

from .intent_gap import detect_intent_gaps
from .outcomes import apply_blindspot_regret, mark_blindspot_regretted, resolve_blindspot_outcomes
from .persist import (
    aggregate_blindspot_events,
    persist_blindspot_candidates,
    read_blindspot_events,
)

__all__ = [
    "aggregate_blindspot_events",
    "apply_blindspot_regret",
    "detect_intent_gaps",
    "mark_blindspot_regretted",
    "persist_blindspot_candidates",
    "read_blindspot_events",
    "resolve_blindspot_outcomes",
]

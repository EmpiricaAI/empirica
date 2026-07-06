"""Source hygiene — sanctifying the epistemic-source corpus.

We've historically been poor at logging epistemic sources, so the corpus carries
legacy / dead / zombie / duplicate entries. Sanctification classifies each active
source (dead / duplicate / zombie / stale / valid) and recommends a lifecycle
action, so the corpus can be trusted enough to join the retrieval surface (the
read-side complement to source-update / source-archive). Report-only by default —
deletions are a judgment that go through review (ARTIFACT_HYGIENE).
"""

from __future__ import annotations

from .sanctify import classify_sources, summarize

__all__ = ["classify_sources", "summarize"]

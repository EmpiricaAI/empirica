"""The finding-log immune-system decay helpers are DISABLED (2026-05-28).

Both previously fired on relatedness (keyword overlap / cosine similarity),
not actual contradiction, so a confirmatory finding decayed the fact/lesson it
confirmed (autoimmune). Converged w/ cortex on decay thread prop_j7y7f4.

**The lexical predicate that was supposed to unblock this was built, measured and
retired.** Over the whole corpus — 6,704 artifacts, 9,064,779 pairs — it fired
ZERO times, and its own motivating example failed its own threshold on English
inflection (v1.13.26 shipped it, v1.13.27 retired it, v1.13.28 removed it). So
these tests are not waiting on someone to finish the predicate: matching text
cannot separate agreement from contradiction, which is what made the original
decay autoimmune. The behavioural signal that could gate a re-enable is a
transaction claim adjudicated `refuted` — a contradiction established by running
the thing, not by reading text.

The helpers early-return before any Qdrant / cold-storage I/O, so inputs that
would previously have triggered decay must now produce no-ops regardless.
"""

from __future__ import annotations

from empirica.cli.command_handlers.artifact_log_commands import (
    _decay_eidetic_by_finding,
    _decay_related_lessons,
)


def test_decay_related_lessons_is_disabled_noop():
    # Keyword-rich finding in a real domain — would previously decay lessons
    # sharing >=2 keywords. Must now be a no-op.
    assert _decay_related_lessons("the sentinel gate blocks praxic tools before check", "sentinel", "proj") == []


def test_decay_eidetic_by_finding_is_disabled_noop():
    # Would previously decay eidetic facts with cosine >= 0.85. Must now be a no-op.
    assert _decay_eidetic_by_finding("proj", "the sentinel gate blocks praxic tools before check", "sentinel") == 0

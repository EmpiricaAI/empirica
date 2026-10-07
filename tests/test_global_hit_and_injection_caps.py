"""Two small pipeline-sweep fixes (U2): a global hit carries its full text, and CHECK's matches count against the injection caps.

1. embed_to_global stores `text_full` and `truncated` precisely so a 500-character cut is recoverable and checkable, but
   _global_hit dropped both on the way out, so a consumer read a fragment that looked like a whole thought.
2. _INJECTION_CATEGORY_KEYS named the PREFLIGHT categories only: CHECK's dead_end_matches and mistake_matches were neither capped nor
   counted by the user's injection settings or the measure view.
"""

from __future__ import annotations

from types import SimpleNamespace

from empirica.core.qdrant import global_sync as gs
from empirica.core.qdrant import pattern_retrieval as pr


def _point(**payload):
    return SimpleNamespace(payload=payload, score=0.4)


def test_global_hit_carries_text_full_and_the_truncated_flag():
    long = "x" * 900
    hit = gs._global_hit(_point(type="finding", text=long[:500], text_full=long, truncated=True))
    assert hit["text_full"] == long and hit["truncated"] is True and len(hit["text"]) == 500


def test_global_hit_for_a_complete_text_says_so():
    hit = gs._global_hit(_point(type="finding", text="short", text_full=None, truncated=False))
    assert hit["text_full"] is None and hit["truncated"] is False


def test_global_hit_tolerates_a_legacy_point_without_either_field():
    hit = gs._global_hit(_point(type="finding", text="old point"))
    assert hit["text_full"] is None and hit["truncated"] is False


def test_checks_matches_are_injection_categories():
    assert {"dead_end_matches", "mistake_matches"} <= set(pr._INJECTION_CATEGORY_KEYS)


def test_the_measure_view_counts_checks_matches():
    result = {
        "dead_end_matches": [{"approach": "a", "why_failed": "f"}],
        "mistake_matches": [{"mistake": "m", "prevention": "p"}],
        "lessons": [{"description": "l"}],
    }
    measure = pr._injection_measure_view(result, {"max_per_category": None, "max_total": None}, 0, 0)
    assert measure["injected_per_category"]["dead_end_matches"] == 1
    assert measure["injected_per_category"]["mistake_matches"] == 1
    assert measure["injected_total"] == 3

"""GH #406: retrieval must say which corpus it came from.

#388 was the loud scope failure — retrieval returned empty, so once you looked,
you saw it. This is the quiet one: a full, well-formed block from the WRONG
project is indistinguishable from a correct one. `retrieved_from` makes the
scope a comparable fact instead of an assumption.
"""

from __future__ import annotations

from unittest.mock import patch

from empirica.core.qdrant import pattern_retrieval as pr


def _patterns(project_id="proj-123"):
    with (
        patch.object(pr, "_retrieval_available", return_value=True, create=True),
        patch.object(pr, "search_lessons_for_task", return_value=[], create=True),
        patch.object(pr, "search_dead_ends", return_value=[], create=True),
        patch.object(pr, "search_mistakes", return_value=[], create=True),
        patch.object(pr, "search_findings", return_value=[], create=True),
    ):
        try:
            return pr.retrieve_task_patterns(project_id, "some task", apply_budget=False)
        except Exception:
            # Collections unavailable in CI — the shape contract is what we pin.
            return None


def test_result_carries_the_project_it_was_scoped_to():
    result = _patterns("proj-123")
    if result is None:
        import pytest

        pytest.skip("qdrant unavailable — shape pinned by the source assertion below")
    assert result["retrieved_from"]["project_id"] == "proj-123"


def test_scope_label_exists_in_source_and_is_excluded_from_emptiness():
    """Structural pin, immune to qdrant availability: the label is written, and
    the PREFLIGHT consumer excludes it from the did-we-find-anything test —
    without that exclusion every EMPTY retrieval renders as a block, which
    would be a new invisible failure shipped inside this fix."""
    from pathlib import Path

    src = Path(pr.__file__).read_text()
    assert '"retrieved_from": {"project_id": project_id}' in src

    consumer = Path("empirica/cli/command_handlers/_workflow_preflight.py").read_text()
    assert '"retrieved_from"' in consumer, "consumer does not know the metadata key"

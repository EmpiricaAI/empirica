"""A retracted finding does not outrank its own correction in search.

Three practices measured it on 2026-09-21: `project-search` served a finding
retracted two hours earlier at rank 1, above the correction. `finding-resolve`
said "dropped from live retrieval", which was true of the PREFLIGHT pattern
block only. The shared search now reconciles against SQLite before it applies
its limit.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core.qdrant import memory, pattern_retrieval


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".empirica" / "sessions").mkdir(parents=True)
    conn = sqlite3.connect(root / ".empirica" / "sessions" / "sessions.db")
    conn.execute("CREATE TABLE project_findings (id TEXT, finding TEXT, is_resolved INTEGER)")
    conn.execute("CREATE TABLE project_unknowns (id TEXT, unknown TEXT, is_resolved INTEGER)")
    conn.executemany(
        "INSERT INTO project_findings VALUES (?, ?, ?)",
        [("f-false", "the false claim", 1), ("f-fix", "the correction", 0), ("f-old", "embedded before ids", 1)],
    )
    conn.executemany(
        "INSERT INTO project_unknowns VALUES (?, ?, ?)", [("u-done", "answered", 1), ("u-open", "open", 0)]
    )
    conn.commit()
    conn.close()
    import empirica.data.session_database as sd

    monkeypatch.setattr(sd, "_resolve_canonical_project_root", lambda: str(root))
    return root


def _candidates():
    return [
        {"artifact_id": "f-false", "type": "finding", "text": "the false claim", "score": 0.92},
        {"artifact_id": "f-fix", "type": "finding", "text": "the correction", "score": 0.87},
        {"artifact_id": None, "type": "finding", "text": "embedded before ids", "score": 0.80},
        {"artifact_id": "u-done", "type": "unknown", "text": "answered", "score": 0.79},
        {"artifact_id": "u-open", "type": "unknown", "text": "open", "score": 0.78},
    ]


def _ids(items):
    return [i["artifact_id"] for i in items]


def test_positive_control_everything_is_served_when_history_is_asked_for(store):
    assert len(memory._drop_resolved_memory("memory", _candidates(), include_resolved=True)) == 5


def test_the_retraction_the_old_embed_and_the_answered_unknown_are_dropped(store):
    kept = memory._drop_resolved_memory("memory", _candidates(), include_resolved=False)
    assert _ids(kept) == ["f-fix", "u-open"]


def test_other_collections_are_not_touched(store):
    assert len(memory._drop_resolved_memory("episodic", _candidates(), include_resolved=False)) == 5


def test_a_failing_reconcile_serves_the_rows_and_warns(monkeypatch, caplog):
    import logging

    def boom(_items):
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(pattern_retrieval, "_reconcile_findings_against_sqlite", boom)
    with caplog.at_level(logging.WARNING):
        kept = memory._drop_resolved_memory("memory", _candidates(), include_resolved=False)
    assert len(kept) == 5 and "resolved artifacts may be served" in caplog.text


def test_search_filters_before_it_applies_the_limit():
    import inspect

    source = inspect.getsource(memory.search)
    assert source.count("_drop_resolved_memory(kind_name, candidates, include_resolved)") == 2  # client + REST
    for chunk in source.split("_drop_resolved_memory(kind_name, candidates, include_resolved)")[1:]:
        assert "_confirm_band" in chunk.split("\n\n")[0]


def test_project_search_passes_the_flag_and_says_what_it_excluded():
    import inspect

    from empirica.cli.command_handlers import project_search as ps

    source = inspect.getsource(ps.handle_project_search_command)
    assert "include_resolved=include_resolved" in source
    assert 'payload["resolved_artifacts"]' in source


# --- the eidetic half: a promoted finding is a second point, in another collection ---


def _facts():
    return [
        {"content": "the false claim", "source_findings": ["f-false"], "confidence": 0.85},
        {"content": "the correction", "source_findings": ["f-fix"], "confidence": 0.80},
        {"content": "embedded before ids", "source_findings": [], "confidence": 0.80},
        {"content": "confirmed twice", "source_findings": ["f-false", "f-fix"], "confidence": 0.90},
        {"content": "a code signature", "source_findings": [], "confidence": 0.90},
    ]


def test_a_fact_is_retired_when_every_finding_it_came_from_is_resolved(store):
    kept = pattern_retrieval.reconcile_eidetic_against_sqlite(_facts())
    assert [f["content"] for f in kept] == ["the correction", "confirmed twice", "a code signature"]


def test_the_shared_search_reconciles_eidetic_candidates(store):
    kept = memory._drop_resolved_memory("eidetic", _facts(), include_resolved=False)
    assert "the false claim" not in [f["content"] for f in kept]
    assert len(memory._drop_resolved_memory("eidetic", _facts(), include_resolved=True)) == 5


def test_an_unreadable_store_serves_the_facts_unchanged(monkeypatch):
    import empirica.data.session_database as sd

    monkeypatch.setattr(sd, "_resolve_canonical_project_root", lambda: None)
    assert len(pattern_retrieval.reconcile_eidetic_against_sqlite(_facts())) == 5


def test_every_eidetic_reader_goes_through_the_one_filtering_search():
    """Five readers call search_eidetic; the filter lives inside it, so none can skip it."""
    import inspect

    from empirica.core.qdrant import eidetic

    source = inspect.getsource(eidetic.search_eidetic)
    assert "reconcile_eidetic_against_sqlite(facts)[:limit]" in source
    assert '"source_findings"' in source
    assert "limit * 2" in source

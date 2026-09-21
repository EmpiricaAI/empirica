"""A finding reaches the same collections whichever verb logged it.

`finding-log` ingested every finding into eidetic; `log-artifacts`, the
documented default for anything with an edge, did not. Measured on a peer store
on 2026-09-21: about 40 findings logged in a day, all batched, none in eidetic.
"""

from __future__ import annotations

import logging

from empirica.cli.command_handlers import artifact_log_commands as alc
from empirica.cli.command_handlers import graph_commands as gc

CONTEXT = {"project_id": "proj-1", "session_id": "sess-1"}


def _record(monkeypatch):
    calls = {"memory": [], "eidetic": []}
    import empirica.core.qdrant.memory as mem

    monkeypatch.setattr(mem, "embed_single_memory_item", lambda **kw: calls["memory"].append(kw) or True)
    monkeypatch.setattr(alc, "_ingest_finding_eidetic", lambda *a: calls["eidetic"].append(a) or "created")
    return calls


def test_a_batch_logged_finding_is_ingested_with_the_single_verbs_arguments(monkeypatch):
    calls = _record(monkeypatch)
    node = {"type": "finding", "data": {"finding": "x is true", "impact": 0.8, "subject": "retrieval"}}
    gc._auto_embed_node(node, "f-1", CONTEXT)
    assert calls["eidetic"] == [("proj-1", "f-1", "x is true", "retrieval", 0.8, "sess-1")]
    assert calls["memory"][0]["impact"] == 0.8


def test_positive_control_other_types_are_embedded_and_not_ingested(monkeypatch):
    calls = _record(monkeypatch)
    gc._auto_embed_node({"type": "unknown", "data": {"unknown": "is y true"}}, "u-1", CONTEXT)
    assert len(calls["memory"]) == 1 and calls["eidetic"] == []


def test_both_verbs_share_one_helper():
    import inspect

    assert "_ingest_finding_eidetic(" in inspect.getsource(gc._ingest_finding_eidetic_like_the_single_verb)
    assert "_ingest_finding_eidetic(" in inspect.getsource(alc.handle_finding_log_command)


def test_a_failed_embed_is_logged_not_swallowed(monkeypatch, caplog):
    import empirica.core.qdrant.memory as mem

    def boom(**_kw):
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(mem, "embed_single_memory_item", boom)
    with caplog.at_level(logging.WARNING):
        gc._auto_embed_node({"type": "finding", "data": {"finding": "x"}}, "f-2", CONTEXT)
    assert "auto-embed of finding f-2 failed" in caplog.text

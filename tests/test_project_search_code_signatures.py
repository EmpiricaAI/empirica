"""project-search does not bury findings under module signatures (David, 2026-10-28; unknown 99851410).

Only `kind="intelligence"` filtered `code_api` rows out of the eidetic collection, and only on the Qdrant-client path. The default search returned,
live, 4 module signatures and 1 finding for one Sentinel query; the REST fallback applied no filter at all. Signatures are now excluded from every
eidetic read unless the caller asks for them with `--type code`, on both transports.
"""

from __future__ import annotations

import pytest

from empirica.core.qdrant import memory as qmem

FACT = {"score": 0.7, "type": "fact", "content": "Sentinel Tier-1 prefix finding", "confidence": 0.8}
SIG_A = {"score": 0.9, "type": "code_api", "content": "Module: empirica.core.sentinel.orchestrator", "confidence": None}
SIG_B = {"score": 0.8, "type": "code_api", "content": "Module: build.lib.empirica.core.sentinel", "confidence": None}
UNTYPED = {"score": 0.6, "type": None, "content": "an older eidetic row with no type", "confidence": 0.5}


@pytest.fixture
def eidetic(monkeypatch):
    """A Qdrant stand-in: both transports return the same three-plus rows, and the client path records the filter it was given."""
    seen: dict = {"filters": []}
    rows = [SIG_A, FACT, SIG_B, UNTYPED]
    monkeypatch.setattr(qmem, "_check_qdrant_available", lambda **_k: True)
    monkeypatch.setattr(qmem, "_get_qdrant_client", lambda **_k: object())

    def client_path(_client, _coll, _pid, _q, _fields, _boost, _limit, query_filter):
        seen["filters"].append(query_filter)
        return [dict(r) for r in rows]

    monkeypatch.setattr(qmem, "_search_single_collection", client_path)
    monkeypatch.setattr(qmem, "_rest_search_collection", lambda *_a, **_k: [dict(r) for r in rows])
    return seen


def _contents(results: dict, key: str = "eidetic") -> list[str]:
    return [r["content"] for r in results[key]]


@pytest.mark.parametrize("kind", ["focused", "all", "eidetic", "intelligence"])
def test_signatures_are_not_in_the_eidetic_results_of_any_default_kind(eidetic, kind):
    out = qmem.search("p", "sentinel", kind=kind, limit=10)
    assert _contents(out) == [FACT["content"], UNTYPED["content"]] or set(_contents(out)) == {
        FACT["content"],
        UNTYPED["content"],
    }
    assert not any("Module:" in c for c in _contents(out))


def test_an_untyped_older_row_is_kept(eidetic):
    """Control: the filter removes code_api, it is not an allow-list of typed rows."""
    assert UNTYPED["content"] in _contents(qmem.search("p", "x", kind="focused", limit=10))


def test_type_code_returns_only_the_signatures(eidetic):
    out = qmem.search("p", "sentinel", kind="code", limit=10)
    assert set(out) == {"eidetic"}
    assert sorted(_contents(out)) == sorted([SIG_A["content"], SIG_B["content"]])


def test_the_row_scope_is_what_covers_the_rest_fallback_which_cannot_take_a_server_filter():
    rows = [dict(SIG_A), dict(FACT), dict(UNTYPED)]
    assert [r["content"] for r in qmem._scope_eidetic("focused", rows)] == [FACT["content"], UNTYPED["content"]]
    assert [r["content"] for r in qmem._scope_eidetic("code", rows)] == [SIG_A["content"]]


def test_other_collections_are_not_touched_by_the_scope(eidetic, monkeypatch):
    """A memory row whose text happens to start with 'Module:' stays: the scope is the eidetic collection's, by payload type."""
    rows = [{"score": 0.5, "type": "finding", "text": "Module: this finding is about a module", "session_id": "s"}]
    monkeypatch.setattr(qmem, "_search_single_collection", lambda *_a, **_k: [dict(r) for r in rows])
    out = qmem.search("p", "module", kind="memory", limit=5)
    assert len(out["memory"]) == 1


def test_the_parser_offers_code_as_a_type():
    import argparse

    from empirica.cli.parsers.checkpoint_parsers import add_checkpoint_parsers

    parser = argparse.ArgumentParser()
    add_checkpoint_parsers(parser.add_subparsers(dest="command"))
    args = parser.parse_args(["project-search", "--task", "x", "--type", "code"])
    assert args.type == "code"


# ── sweep S1: the other eidetic readers (PREFLIGHT/CHECK/bootstrap chokepoint, and the cross-project search behind --global) ───────


class _P:
    def __init__(self, i, type_, content):
        self.id, self.score = i, 0.5
        self.payload = {"type": type_, "content": content, "confidence": 0.7}


class _Resp:
    def __init__(self, points):
        self.points = points


class _Client:
    def __init__(self, points):
        self.points, self.filters = points, []

    def collection_exists(self, _n):
        return True

    def query_points(self, **kw):
        self.filters.append(kw.get("query_filter"))
        return _Resp(self.points)


POINTS = [_P("1", "code_api", "Module: a.b"), _P("2", "fact", "a real fact"), _P("3", None, "an untyped older fact")]


def _patch_eidetic(monkeypatch, client):
    import empirica.core.qdrant.eidetic as eid
    import empirica.core.qdrant.pattern_retrieval as pr

    monkeypatch.setattr(eid, "_check_qdrant_available", lambda *a, **k: True)
    monkeypatch.setattr(eid, "_get_qdrant_client", lambda *a, **k: client)
    monkeypatch.setattr(eid, "_get_embedding_safe", lambda _q: [0.1])
    monkeypatch.setattr(pr, "reconcile_eidetic_against_sqlite", lambda facts: facts)
    return eid


def test_the_shared_eidetic_reader_leaves_signatures_out_unless_asked_for_them(monkeypatch):
    client = _Client(POINTS)
    eid = _patch_eidetic(monkeypatch, client)
    assert [f["content"] for f in eid.search_eidetic("p", "q", limit=10)] == ["a real fact", "an untyped older fact"]
    assert client.filters[-1] is not None, "and the server is asked not to return them, so they do not eat the limit"
    asked = eid.search_eidetic("p", "q", fact_type="code_api", limit=10)
    assert [f["content"] for f in asked] == ["Module: a.b"] or {f["type"] for f in asked} <= {"code_api", "fact", None}


def test_cross_project_search_leaves_signatures_out(monkeypatch):
    import types

    import empirica.core.qdrant.global_sync as gs

    colls = types.SimpleNamespace(collections=[types.SimpleNamespace(name="project_abc_eidetic")])

    class _C:
        def get_collections(self):
            return colls

    monkeypatch.setattr(gs, "_check_qdrant_available", lambda *a, **k: True)
    monkeypatch.setattr(gs, "_get_embedding_safe", lambda _q: [0.1])
    monkeypatch.setattr(gs, "_get_qdrant_client", lambda *a, **k: _C())
    monkeypatch.setattr(gs, "_query_collection_safe", lambda *a, **k: list(POINTS))
    out = gs.search_cross_project("q", collections_to_search=["eidetic"], limit=10)
    assert sorted(r["content"] for r in out) == ["a real fact", "an untyped older fact"]

"""Resolving an artifact stamps its vector points, for readers that bypass search().

The payload's is_resolved was written once at embed and frozen, so a reader
going to Qdrant directly saw a retracted finding as live. Read-time reconcile
in the two search functions is the guarantee; this is the second layer. The
client is faked at the module the sync imports it from, so no Qdrant is needed.
"""

from __future__ import annotations

import pytest

from empirica.core.qdrant import resolution_sync
from empirica.core.qdrant.point_ids import artifact_point_id

FID = "aaaaaaaa-0000-4000-8000-000000000001"


class _Client:
    def __init__(self, collections):
        self.collections = set(collections)
        self.calls = []

    def collection_exists(self, name):
        return name in self.collections

    def set_payload(self, collection_name, payload, points):
        if collection_name.endswith("_eidetic") and "boom" in self.collections:
            raise RuntimeError("no such point")
        self.calls.append((collection_name, payload, points))


@pytest.fixture
def qdrant(monkeypatch):
    import empirica.core.qdrant.connection as qc

    holder = {}

    def make(collections):
        holder["client"] = _Client(collections)
        monkeypatch.setattr(qc, "_check_qdrant_available", lambda *a, **k: True)
        monkeypatch.setattr(qc, "_get_qdrant_client", lambda *a, **k: holder["client"])
        return holder["client"]

    return make


def test_both_points_are_stamped_with_kind_and_pointer(qdrant):
    from empirica.core.qdrant.collections import _eidetic_collection, _memory_collection

    client = qdrant([_memory_collection("p"), _eidetic_collection("p")])
    done = resolution_sync.mark_resolved_in_qdrant("p", FID, resolution_kind="retracted", superseded_by="x")
    assert done == {"memory": True, "eidetic": True}
    for _coll, payload, points in client.calls:
        assert payload == {"is_resolved": True, "resolution_kind": "retracted", "superseded_by": "x"}
        assert points == [artifact_point_id(FID)]


def test_a_missing_collection_or_point_is_skipped_not_fatal(qdrant):
    from empirica.core.qdrant.collections import _memory_collection

    qdrant([_memory_collection("p")])  # no eidetic collection at all
    assert resolution_sync.mark_resolved_in_qdrant("p", FID) == {"memory": True, "eidetic": False}


def test_an_eidetic_failure_does_not_undo_the_memory_stamp(qdrant):
    from empirica.core.qdrant.collections import _eidetic_collection, _memory_collection

    qdrant([_memory_collection("p"), _eidetic_collection("p"), "boom"])
    assert resolution_sync.mark_resolved_in_qdrant("p", FID) == {"memory": True, "eidetic": False}


def test_no_qdrant_is_quiet(monkeypatch):
    import empirica.core.qdrant.connection as qc

    monkeypatch.setattr(qc, "_check_qdrant_available", lambda *a, **k: False)
    assert resolution_sync.mark_resolved_in_qdrant("p", FID) == {"memory": False, "eidetic": False}
    assert resolution_sync.mark_resolved_in_qdrant(None, FID) == {"memory": False, "eidetic": False}


def test_every_resolution_writer_calls_the_sync():
    import inspect

    from empirica.api.routes import artifacts
    from empirica.cli.command_handlers import graph_commands
    from empirica.data.repositories import breadcrumbs

    repo = inspect.getsource(breadcrumbs.BreadcrumbRepository)
    assert repo.count("self._sync_resolution_to_qdrant(") == 2  # finding + unknown
    assert inspect.getsource(graph_commands).count('_sync_resolution_to_qdrant(db, "project_') == 2
    assert "_sync_resolution_to_qdrant(" in inspect.getsource(artifacts)

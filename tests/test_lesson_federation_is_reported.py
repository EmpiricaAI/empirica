"""
`lesson-create` must say whether the lesson reached the shared pool.

Federation used to run only in the POSTFLIGHT sweep. Between create and
POSTFLIGHT the lesson existed, carried `sharing_policy: org`, read as shared in
every local surface, and was invisible to every peer — while the receipt handed
back an id and said nothing. Handing that id to a peer is the obvious next move
and it failed for a window closing at POSTFLIGHT, or never.

A peer's `--from-global` ingest of an id published minutes earlier was correctly
refused on 2026-09-05. No local test could have caught it: every read on the
authoring side resolves locally and passes.

These assert the CONTRACT — a caller can distinguish published, deferred and
not-requested — rather than which mechanism does the publishing.
"""

from __future__ import annotations

import types

import pytest

from empirica.cli.command_handlers import lesson_commands as lc


def _lesson(policy):
    return types.SimpleNamespace(id="abc123", name="n", sharing_policy=policy)


@pytest.mark.parametrize("policy", ["private", "project", None])
def test_unfederated_policy_is_not_reported_as_a_failure(policy):
    out = lc._federate_now(_lesson(policy))
    assert out["state"] == "not_requested"
    assert out["sharing_policy"] == policy


@pytest.mark.parametrize("policy", ["org", "public"])
def test_successful_publish_says_the_id_is_shareable(policy, monkeypatch):
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.context",
        staticmethod(lambda *a, **k: {"project_id": "p1"}),
    )
    monkeypatch.setattr(
        "empirica.core.qdrant.global_sync.sync_lessons_to_global",
        lambda pid: {"eligible": 1, "synced": 1, "failed": 0},
    )
    out = lc._federate_now(_lesson(policy))
    assert out["state"] == "published"
    assert out["sync"]["synced"] == 1


def test_a_failed_publish_is_DEFERRED_not_silently_ok(monkeypatch):
    """The defect this file exists for: a create that could not share, reporting success."""
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.context",
        staticmethod(lambda *a, **k: {"project_id": "p1"}),
    )
    monkeypatch.setattr(
        "empirica.core.qdrant.global_sync.sync_lessons_to_global",
        lambda pid: {"eligible": 1, "synced": 0, "failed": 1, "skipped_reason": None},
    )
    out = lc._federate_now(_lesson("org"))
    assert out["state"] == "deferred", "a failed sync must not read as published"
    assert "failed to embed" in out["detail"]


def test_an_unreachable_pool_is_deferred_and_says_why(monkeypatch):
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.context",
        staticmethod(lambda *a, **k: {"project_id": "p1"}),
    )

    def boom(pid):
        raise RuntimeError("qdrant unreachable")

    monkeypatch.setattr("empirica.core.qdrant.global_sync.sync_lessons_to_global", boom)
    out = lc._federate_now(_lesson("org"))
    assert out["state"] == "deferred"
    assert "qdrant unreachable" in out["detail"]
    assert "POSTFLIGHT will retry" in out["detail"], "a deferral must name the path that closes it"


def test_no_project_id_is_deferred_not_published(monkeypatch):
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.context",
        staticmethod(lambda *a, **k: {}),
    )
    out = lc._federate_now(_lesson("org"))
    assert out["state"] == "deferred"


def test_the_three_states_are_distinguishable(monkeypatch):
    """
    Positive control for the parametrized tests above.

    Each asserts one state in isolation, which they would also do if the helper
    returned a constant. Assert that the states actually DIFFER across inputs
    before trusting any single one of them.
    """
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.context",
        staticmethod(lambda *a, **k: {"project_id": "p1"}),
    )
    monkeypatch.setattr(
        "empirica.core.qdrant.global_sync.sync_lessons_to_global",
        lambda pid: {"eligible": 1, "synced": 1, "failed": 0},
    )
    seen = {
        lc._federate_now(_lesson("private"))["state"],
        lc._federate_now(_lesson("org"))["state"],
    }
    monkeypatch.setattr(
        "empirica.core.qdrant.global_sync.sync_lessons_to_global",
        lambda pid: {"eligible": 1, "synced": 0, "failed": 1},
    )
    seen.add(lc._federate_now(_lesson("org"))["state"])
    assert seen == {"not_requested", "published", "deferred"}

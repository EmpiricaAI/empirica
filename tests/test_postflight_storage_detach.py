"""POSTFLIGHT storage pipeline runs detached, off the response critical path.

The storage pipeline (embeds + global sync — measured ~5.7s) produces nothing
the POSTFLIGHT response consumes, so it's spawned fire-and-forget via
_postflight_storage_worker. Guards:
  - the spawn stages a pickled payload + detaches (start_new_session), not inline
  - staging/spawn failure falls back to a synchronous run (never drop the work)
  - the worker runs the pipeline from the payload + cleans up the temp file
"""

from __future__ import annotations

import os
import pickle
import tempfile
from unittest.mock import MagicMock, patch

from empirica.cli.command_handlers import _postflight_storage_worker as worker
from empirica.cli.command_handlers import _workflow_postflight as wf

_V = {"know": 0.8, "do": 0.8, "context": 0.8, "clarity": 0.8}


class _NotJsonSerializable:
    """Stand-in for grounded_verification's EvidenceBundle — pickles fine, but
    json.dump would raise. This is exactly the object that made the first cut of
    the detach silently fall back to inline on every real POSTFLIGHT."""

    def __init__(self, tag):
        self.tag = tag


def test_spawn_writes_payload_and_detaches():
    seen = {}

    def fake_popen(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        with open(args[-1], "rb") as pf:
            seen["payload"] = pickle.load(pf)  # noqa: S301
        return MagicMock()

    with (
        patch("subprocess.Popen", side_effect=fake_popen),
        patch.object(wf, "_run_postflight_storage_pipeline") as inline,
    ):
        wf._spawn_detached_storage_pipeline("sid-1", _V, {"know": 0.1}, "why", {"evidence_count": 5}, "ckpt")

    assert seen["kwargs"]["start_new_session"] is True
    assert "_postflight_storage_worker" in seen["args"][2]
    assert seen["payload"]["session_id"] == "sid-1"
    assert seen["payload"]["grounded_verification"] == {"evidence_count": 5}
    assert seen["payload"]["checkpoint_id"] == "ckpt"
    assert "postflight_confidence" not in seen["payload"], "the retired derived scalar is not staged for the worker"
    inline.assert_not_called()  # detached, NOT run inline


def test_spawn_detaches_with_non_json_serializable_grounded_verification():
    """Regression: grounded_verification is an EvidenceBundle (not JSON-safe).
    The payload must pickle + detach, NOT silently fall back to inline."""
    seen = {}

    def fake_popen(args, **kwargs):
        with open(args[-1], "rb") as pf:
            seen["payload"] = pickle.load(pf)  # noqa: S301
        return MagicMock()

    gv = _NotJsonSerializable("bundle")
    with (
        patch("subprocess.Popen", side_effect=fake_popen),
        patch.object(wf, "_run_postflight_storage_pipeline") as inline,
    ):
        wf._spawn_detached_storage_pipeline("sid-gv", _V, {}, "why", gv, None)

    inline.assert_not_called()  # detached — did NOT fall back to inline
    assert isinstance(seen["payload"]["grounded_verification"], _NotJsonSerializable)
    assert seen["payload"]["grounded_verification"].tag == "bundle"


def test_spawn_falls_back_inline_on_popen_failure():
    with (
        patch("subprocess.Popen", side_effect=OSError("cannot spawn")),
        patch.object(wf, "_run_postflight_storage_pipeline") as inline,
    ):
        wf._spawn_detached_storage_pipeline("sid-2", _V, {}, "why", None, None)

    inline.assert_called_once()  # spawn failed → work runs synchronously, never dropped
    assert inline.call_args.kwargs["session_id"] == "sid-2"


def test_spawn_falls_back_inline_when_staging_fails():
    with (
        patch("tempfile.mkstemp", side_effect=OSError("no temp")),
        patch.object(wf, "_run_postflight_storage_pipeline") as inline,
    ):
        wf._spawn_detached_storage_pipeline("sid-3", _V, {}, "why", None, None)

    inline.assert_called_once()


def test_worker_runs_pipeline_and_cleans_up():
    fd, path = tempfile.mkstemp(suffix=".pkl")
    with os.fdopen(fd, "wb") as f:
        pickle.dump(
            {
                "session_id": "sid-4",
                "vectors": _V,
                "deltas": {},
                "reasoning": "r",
                "grounded_verification": None,
                "checkpoint_id": None,
            },
            f,
        )

    with patch.object(wf, "_run_postflight_storage_pipeline") as pipe:
        worker.main(path)

    pipe.assert_called_once()
    assert pipe.call_args.kwargs["session_id"] == "sid-4"
    assert not os.path.exists(path)  # temp payload cleaned up


def test_worker_unreadable_payload_is_noop():
    # Missing file → return quietly, never call the pipeline.
    with patch.object(wf, "_run_postflight_storage_pipeline") as pipe:
        worker.main("/nonexistent/path/payload.json")
    pipe.assert_not_called()


# ── the MEMORY.md hot cache rides the detached pipeline (goal eb8e1029) ────────────────────────────────────────────────
# Measured in fresh processes: ~0.65 s on the POSTFLIGHT response path, for writes nothing in the response reads.


def test_the_result_builder_no_longer_updates_the_hot_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(wf, "_postflight_update_memory_hot_cache", lambda *a, **k: calls.append(a))
    wf._build_postflight_result(
        session_id="sid-r",
        internal_consistency={},
        deltas={},
        trajectory_issues=[],
        grounded_verification=None,
        compliance_result=None,
        compliance_error=None,
        postflight_grounded_vectors=None,
        postflight_grounded_rationale=None,
        vectors={"know": 0.9},
        resolved_project_path="/proj",
    )
    assert calls == []


def test_the_spawn_stages_the_project_path_for_the_worker():
    seen = {}

    def fake_popen(args, **kwargs):
        with open(args[-1], "rb") as pf:
            seen["payload"] = pickle.load(pf)  # noqa: S301
        return MagicMock()

    with patch("subprocess.Popen", side_effect=fake_popen), patch.object(wf, "_run_postflight_storage_pipeline"):
        wf._spawn_detached_storage_pipeline("sid-p", _V, {}, "why", None, None, project_path="/proj")
    assert seen["payload"]["project_path"] == "/proj"


def test_the_inline_fallback_passes_the_project_path():
    with (
        patch("tempfile.mkstemp", side_effect=OSError("disk full")),
        patch.object(wf, "_run_postflight_storage_pipeline") as inline,
    ):
        wf._spawn_detached_storage_pipeline("sid-f", _V, {}, "why", None, None, project_path="/proj")
    assert inline.call_args.kwargs["project_path"] == "/proj"


def test_the_worker_passes_the_project_path_and_tolerates_an_old_payload():
    for payload, expected in (({"project_path": "/proj"}, "/proj"), ({}, None)):
        fd, path = tempfile.mkstemp(suffix=".pkl")
        base = {"session_id": "s", "vectors": _V, "deltas": {}, "reasoning": "r", "grounded_verification": None}
        with os.fdopen(fd, "wb") as f:
            pickle.dump({**base, "checkpoint_id": None, **payload}, f)
        with patch.object(wf, "_run_postflight_storage_pipeline") as pipe:
            worker.main(path)
        assert pipe.call_args.kwargs["project_path"] == expected


def test_the_pipeline_updates_the_hot_cache_even_when_the_session_is_unreadable(monkeypatch):
    """The inline step ran whatever the session lookup said; the pipeline returns early without a project id, and the
    hot cache must not go with it."""
    calls = []
    monkeypatch.setattr(wf, "_postflight_update_memory_hot_cache", lambda sid, path: calls.append((sid, path)))
    monkeypatch.setattr(wf, "_get_db_for_session", MagicMock(side_effect=RuntimeError("no db")))
    wf._run_postflight_storage_pipeline("sid-h", _V, {}, "r", None, None, project_path="/proj")
    assert calls == [("sid-h", "/proj")]

"""POSTFLIGHT storage pipeline runs detached, off the response critical path.

The storage pipeline (embeds + global sync — measured ~5.7s) produces nothing
the POSTFLIGHT response consumes, so it's spawned fire-and-forget via
_postflight_storage_worker. Guards:
  - the spawn stages a JSON payload + detaches (start_new_session), not inline
  - staging/spawn failure falls back to a synchronous run (never drop the work)
  - the worker runs the pipeline from the payload + cleans up the temp file
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

from empirica.cli.command_handlers import _postflight_storage_worker as worker
from empirica.cli.command_handlers import _workflow_postflight as wf

_V = {"know": 0.8, "do": 0.8, "context": 0.8, "clarity": 0.8}


def test_spawn_writes_payload_and_detaches():
    seen = {}

    def fake_popen(args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        with open(args[-1], encoding="utf-8") as pf:
            seen["payload"] = json.load(pf)
        return MagicMock()

    with (
        patch("subprocess.Popen", side_effect=fake_popen),
        patch.object(wf, "_run_postflight_storage_pipeline") as inline,
    ):
        wf._spawn_detached_storage_pipeline("sid-1", _V, {"know": 0.1}, "why", {"evidence_count": 5}, 0.82, "ckpt")

    assert seen["kwargs"]["start_new_session"] is True
    assert "_postflight_storage_worker" in seen["args"][2]
    assert seen["payload"]["session_id"] == "sid-1"
    assert seen["payload"]["grounded_verification"] == {"evidence_count": 5}
    assert seen["payload"]["checkpoint_id"] == "ckpt"
    inline.assert_not_called()  # detached, NOT run inline


def test_spawn_falls_back_inline_on_popen_failure():
    with (
        patch("subprocess.Popen", side_effect=OSError("cannot spawn")),
        patch.object(wf, "_run_postflight_storage_pipeline") as inline,
    ):
        wf._spawn_detached_storage_pipeline("sid-2", _V, {}, "why", None, 0.8, None)

    inline.assert_called_once()  # spawn failed → work runs synchronously, never dropped
    assert inline.call_args.kwargs["session_id"] == "sid-2"


def test_spawn_falls_back_inline_when_staging_fails():
    with (
        patch("tempfile.mkstemp", side_effect=OSError("no temp")),
        patch.object(wf, "_run_postflight_storage_pipeline") as inline,
    ):
        wf._spawn_detached_storage_pipeline("sid-3", _V, {}, "why", None, 0.8, None)

    inline.assert_called_once()


def test_worker_runs_pipeline_and_cleans_up():
    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(
            {
                "session_id": "sid-4",
                "vectors": _V,
                "deltas": {},
                "reasoning": "r",
                "grounded_verification": None,
                "postflight_confidence": 0.8,
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

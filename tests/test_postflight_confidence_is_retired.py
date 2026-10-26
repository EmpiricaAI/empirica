"""POSTFLIGHT and the PREFLIGHT snapshot no longer report `1 - uncertainty` as a measured confidence (goal d275a838).

CHECK's `confidence` was `1 - uncertainty` on 1,933 of 1,933 rows and was retired in 1.14.17. The same relabelling stood at two more phases:
`postflight_confidence` equalled `1 - uncertainty` on 3,244 of 3,244 POSTFLIGHT rows that carried it, and the PREFLIGHT snapshot stored
`semantic={'confidence': 1 - uncertainty}`. Snapshot narratives are what later prompts retrieve, so a future practitioner read a derived
scalar as a past measurement. No peer repository reads either field.

What stays is `internal_consistency`: whether `completion` and `1 - uncertainty`, two self-reports that ought to agree, do. It is computed
directly, says what it compares, and answers `not_assessed` when either vector was not supplied instead of scoring an invented 0.5.
"""

from __future__ import annotations

import json
import pickle
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from empirica.cli.command_handlers import _workflow_postflight as wp
from empirica.cli.command_handlers import _workflow_preflight as wf

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── internal_consistency, computed directly ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("completion", "uncertainty", "expected"),
    [
        (0.90, 0.10, "good"),  # completion .9 vs 1 - u = .9
        (0.90, 0.35, "moderate"),  # gap .25
        (0.90, 0.60, "poor"),  # gap .50
        (0.10, 0.10, "poor"),  # almost nothing done yet almost no doubt: the two self-reports disagree
        (0.50, 0.50, "good"),
    ],
)
def test_internal_consistency_compares_completion_with_one_minus_uncertainty(completion, uncertainty, expected):
    label, basis = wp._internal_consistency({"completion": completion, "uncertainty": uncertainty})
    assert label == expected
    assert "completion" in basis and "uncertainty" in basis, "it says what it compares"


@pytest.mark.parametrize(
    "vectors",
    [
        {},
        {"completion": 0.9},
        {"uncertainty": 0.1},
        {"completion": None, "uncertainty": 0.1},
        {"completion": "high", "uncertainty": 0.1},
    ],
)
def test_a_missing_or_non_numeric_vector_is_not_assessed_not_scored_against_an_invented_half(vectors):
    label, _basis = wp._internal_consistency(vectors)
    assert label == "not_assessed"


# ── the result, the bus event, the snapshots ────────────────────────────────────────────────────────────────────


def _result(**kw):
    args = {
        "session_id": "s",
        "internal_consistency": "good",
        "deltas": {},
        "trajectory_issues": [],
        "grounded_verification": None,
        "compliance_result": None,
        "compliance_error": None,
        "postflight_grounded_vectors": None,
        "postflight_grounded_rationale": None,
        "vectors": {},
        "resolved_project_path": None,
    }
    args.update(kw)
    return wp._build_postflight_result(**args)


def test_the_result_carries_internal_consistency_and_its_basis_but_no_postflight_confidence(monkeypatch):
    monkeypatch.setattr(wp, "_postflight_update_memory_hot_cache", lambda *a, **k: None)
    result = _result()
    assert "postflight_confidence" not in result
    assert result["internal_consistency"] == "good"
    assert "completion" in result["internal_consistency_basis"]


def test_the_bus_event_does_not_publish_it(monkeypatch):
    import empirica.core.epistemic_bus as bus_mod

    published = []

    class _Bus:
        def publish(self, event):
            published.append(event)

    monkeypatch.setattr(bus_mod, "get_global_bus", lambda: _Bus())
    monkeypatch.setattr("empirica.core.bus_persistence.wire_persistent_observers", lambda **k: None)
    wp._postflight_publish_bus_event("s", "t", {"know": 0.5}, {}, "good")
    assert published and "postflight_confidence" not in published[0].data
    assert published[0].data["internal_consistency"] == "good"


def _capture_snapshot_summary(monkeypatch):
    seen = {}

    class _Provider:
        def create_snapshot_from_session(self, session_id, context_summary, cascade_phase, domain_vectors=None):
            seen["semantic"] = dict(context_summary.semantic)

            class _S:
                snapshot_id = "x"
                vectors = None

            return _S()

        def save_snapshot(self, snapshot):
            pass

    monkeypatch.setattr("empirica.data.snapshot_provider.EpistemicSnapshotProvider", _Provider)
    return seen


def test_the_preflight_snapshot_carries_its_phase_and_no_confidence(monkeypatch):
    seen = _capture_snapshot_summary(monkeypatch)
    wf._preflight_create_snapshot("s-1", {"know": 0.5, "uncertainty": 0.2}, "r", "cp", "tx")
    assert seen["semantic"] == {"phase": "PREFLIGHT"}


def test_the_postflight_snapshot_carries_its_phase_and_no_confidence(monkeypatch):
    seen = _capture_snapshot_summary(monkeypatch)

    class _Db:
        def get_session(self, _sid):
            return {"session_id": "s-1"}

        def close(self):
            pass

    monkeypatch.setattr(wp, "_get_db_for_session", lambda _sid: _Db())
    wp._pipeline_epistemic_snapshot("s-1", {"know": 0.5}, {}, "reasoning", "cp")
    assert seen["semantic"] == {"phase": "POSTFLIGHT"}


# ── the detached storage worker: tolerant in both directions while old and new producers overlap ───────────────


@pytest.mark.parametrize("with_key", [False, True])
def test_the_storage_worker_accepts_a_payload_with_or_without_the_retired_key(tmp_path, monkeypatch, with_key):
    from empirica.cli.command_handlers import _postflight_storage_worker as worker

    payload = {
        "session_id": "s",
        "vectors": {},
        "deltas": {},
        "reasoning": "r",
        "grounded_verification": None,
        "checkpoint_id": "cp",
    }
    if with_key:
        payload["postflight_confidence"] = 0.8  # what a producer from before this change still writes
    path = tmp_path / "payload.pkl"
    path.write_bytes(pickle.dumps(payload))
    calls = []
    monkeypatch.setattr(wp, "_run_postflight_storage_pipeline", lambda **kw: calls.append(kw))
    worker.main(str(path))
    assert len(calls) == 1 and "postflight_confidence" not in calls[0]


def test_the_deprecated_assessment_logger_no_longer_stores_the_scalar(tmp_path):
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    try:
        sid = db.create_session(ai_id="a", project_id="p")
        db.log_postflight_assessment(
            session_id=sid,
            cascade_id=None,
            task_summary="done",
            vectors={"know": 0.9, "uncertainty": 0.1},
            calibration_accuracy="high",
        )
        raw = db.conn.execute(
            "SELECT reflex_data FROM reflexes WHERE session_id = ? AND phase = 'POSTFLIGHT'", (sid,)
        ).fetchone()[0]
    finally:
        db.close()
    assert "postflight_confidence" not in json.loads(raw)


# ── one real transaction ────────────────────────────────────────────────────────────────────────────────────────

VECTORS = dict.fromkeys(
    [
        "know",
        "do",
        "context",
        "clarity",
        "coherence",
        "signal",
        "density",
        "state",
        "change",
        "completion",
        "impact",
        "engagement",
    ],
    0.6,
) | {"uncertainty": 0.3}


def test_a_real_postflight_neither_returns_nor_stores_the_derived_scalar(tmp_path):
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "PYTHONPATH": str(REPO_ROOT), "EMPIRICA_NO_TELEMETRY": "1"}

    def run(args, stdin=None):
        return subprocess.run(
            [sys.executable, "-m", "empirica.cli", *args],
            input=stdin,
            capture_output=True,
            text=True,
            cwd=project,
            env=env,
            timeout=180,
        )

    subprocess.run(["git", "init", "-q"], cwd=project, check=True, env=env)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"],
        cwd=project,
        check=True,
        env=env,
    )
    assert run(["project-init", "--non-interactive"]).returncode == 0
    session = json.loads(run(["session-create", "--ai-id", "retired-scalar", "--output", "json"]).stdout)["session_id"]
    pre = {
        "session_id": session,
        "task_context": "t",
        "work_type": "research",
        "vectors": VECTORS,
        "reasoning": "r",
        "claims": [{"claim": "c", "grounding": "read", "ref": "x"}],
    }
    assert json.loads(run(["preflight-submit", "-"], json.dumps(pre)).stdout)["ok"] is True
    post = {"session_id": session, "vectors": VECTORS | {"completion": 0.7}, "reasoning": "r"}
    result = json.loads(run(["postflight-submit", "-"], json.dumps(post)).stdout)
    assert result["ok"] is True
    assert "postflight_confidence" not in result
    assert result["internal_consistency"] in {"good", "moderate", "poor"}
    assert "completion" in result["internal_consistency_basis"]
    conn = sqlite3.connect(str(project / ".empirica" / "sessions" / "sessions.db"))
    try:
        stored = " ".join(
            r[0] or "" for r in conn.execute("SELECT reflex_data FROM reflexes WHERE phase = 'POSTFLIGHT'")
        )
    finally:
        conn.close()
    assert stored.strip() and "postflight_confidence" not in stored

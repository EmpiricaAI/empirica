"""EPISTEMIC FOCUS relevance wiring: task_context must survive to the injectors.

The relevance machinery (fetch_relevance + additive blend) existed and was
sound, but two links were missing (2026-08-16):

1. task_context was never durably persisted — it flowed through the PREFLIGHT
   retrieval query + bus event and evaporated, so post-compact had no task
   statement to rank against and the block degraded to recency+impact.
2. The transaction-continue and check prompts passed neither task_context nor
   project_id to format_epistemic_focus, so even the announced degradation ran
   on every compaction of an open transaction.

These pin the persistence (PREFLIGHT checkpoint metadata) and the extraction
(_load_dynamic_context's preflight_task_context).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture()
def post_compact():
    sys.path.insert(0, str(_HOOK_DIR))
    spec = importlib.util.spec_from_file_location("post_compact_test", _HOOK_DIR / "post-compact.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── persistence: PREFLIGHT checkpoint carries task_context ────────────────────


def test_preflight_checkpoint_metadata_carries_task_context(monkeypatch):
    from empirica.cli.command_handlers import _workflow_preflight as wp

    captured = {}

    class _FakeLogger:
        def __init__(self, **kw):
            pass

        def add_checkpoint(self, phase, vectors, metadata):
            captured["phase"] = phase
            captured["metadata"] = metadata
            return "ckpt-1"

    import empirica.core.canonical.git_enhanced_reflex_logger as gerl

    monkeypatch.setattr(gerl, "GitEnhancedReflexLogger", _FakeLogger)

    wp._preflight_create_checkpoint("s1", {"know": 0.5}, "reasoning", "tx-1", task_context="Fix the widget")
    assert captured["metadata"]["task_context"] == "Fix the widget"
    assert captured["metadata"]["transaction_id"] == "tx-1"


def test_preflight_checkpoint_omits_empty_task_context(monkeypatch):
    """No task_context → the key is absent, not an empty string (readers use
    truthiness; an empty-string field would read as present-but-blank)."""
    from empirica.cli.command_handlers import _workflow_preflight as wp

    captured = {}

    class _FakeLogger:
        def __init__(self, **kw):
            pass

        def add_checkpoint(self, phase, vectors, metadata):
            captured["metadata"] = metadata
            return "ckpt-1"

    import empirica.core.canonical.git_enhanced_reflex_logger as gerl

    monkeypatch.setattr(gerl, "GitEnhancedReflexLogger", _FakeLogger)

    wp._preflight_create_checkpoint("s1", {}, "r", "tx-1", task_context=None)
    assert "task_context" not in captured["metadata"]


# ── extraction: _load_dynamic_context surfaces preflight_task_context ─────────


def _seed_db(tmp_path, monkeypatch, reflex_rows):
    """Full-schema session db (SessionDatabase initializes schema) + seed rows."""
    from empirica.data.session_database import SessionDatabase

    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(db_path))
    db = SessionDatabase(db_path=str(db_path))
    cur = db.conn.cursor()
    cur.execute(
        "INSERT INTO sessions (session_id, ai_id, project_id, start_time, components_loaded) "
        "VALUES ('s1', 'ai', 'p1', ?, '[]')",
        (time.time(),),
    )
    for i, data in enumerate(reflex_rows):
        cur.execute(
            "INSERT INTO reflexes (session_id, phase, timestamp, reflex_data) VALUES ('s1', 'PREFLIGHT', ?, ?)",
            (time.time() + i, json.dumps(data)),
        )
    db.conn.commit()
    db.close()


def test_load_dynamic_context_extracts_latest_task_context(post_compact, tmp_path, monkeypatch):
    _seed_db(
        tmp_path,
        monkeypatch,
        [
            {"task_context": "older task", "transaction_id": "t1"},
            {"task_context": "Fix the flux capacitor", "transaction_id": "t2"},  # latest
        ],
    )
    ctx = post_compact._load_dynamic_context("s1", "ai", {})
    assert ctx.get("preflight_task_context") == "Fix the flux capacitor"


def test_load_dynamic_context_none_when_field_absent(post_compact, tmp_path, monkeypatch):
    """Pre-fix reflex rows (no task_context key) → None, so callers fall back to
    last_task and the degradation note still announces the mode."""
    _seed_db(tmp_path, monkeypatch, [{"transaction_id": "t1", "prompt": "reasoning text"}])
    ctx = post_compact._load_dynamic_context("s1", "ai", {})
    assert ctx.get("preflight_task_context") is None

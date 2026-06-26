"""Regression: assumption-log must thread --confidence through to storage.

Bug: the handler resolved confidence as
``(config or {}).get("confidence", 0.5) or getattr(args, "confidence", 0.5)``.
In CLI mode ``config`` is empty, so ``.get(..., 0.5)`` returns the *truthy* 0.5
and the ``or`` short-circuits — the actual ``--confidence`` was silently dropped
(every CLI assumption stored 0.5). A "default masks dropped intent" failure.
"""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

from empirica.cli.command_handlers import artifact_log_commands as alc


def _ctx(db):
    return {
        "db": db, "project_id": "p", "session_id": "s", "ai_id": "test",
        "goal_id": None, "transaction_id": None,
        "entity_type": None, "entity_id": None,
        "visibility": "shared", "via": None, "output_format": "text",
    }


def _stored_confidence(monkeypatch, *, args_confidence, config_data=None):
    db = MagicMock()
    db.log_assumption.return_value = "assump-1"
    monkeypatch.setattr(alc, "_parse_config_input", lambda args: config_data)
    monkeypatch.setattr(alc, "_resolve_artifact_context", lambda *a, **k: _ctx(db))
    monkeypatch.setattr(alc, "_warn_unsourced_citations_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(alc, "_collect_edges_from_args", lambda *a, **k: [])
    monkeypatch.setattr(alc, "_suggest_links_safe", lambda *a, **k: [])
    args = argparse.Namespace(
        assumption="x", confidence=args_confidence, domain=None,
        description=None, epistemic_source=None, config=None,
    )
    alc.handle_assumption_log_command(args)
    return db.log_assumption.call_args.kwargs["confidence"]


def test_cli_confidence_is_stored(monkeypatch):
    # the bug stored 0.5 regardless of the flag
    assert _stored_confidence(monkeypatch, args_confidence=0.7) == 0.7


def test_cli_confidence_zero_preserved(monkeypatch):
    # explicit 0.0 is a valid confidence — must not be clobbered to the default
    assert _stored_confidence(monkeypatch, args_confidence=0.0) == 0.0


def test_stdin_confidence_overrides_args(monkeypatch):
    # AI-first (stdin JSON) mode: config_data wins over the argparse default
    got = _stored_confidence(
        monkeypatch, args_confidence=0.5, config_data={"assumption": "x", "confidence": 0.9}
    )
    assert got == 0.9

"""Tests for the EWM loader's SessionStart inbox-lead block (prop_pu4xrog).

The SessionStart greet-prime dominates injected wakes, so pending mesh messages
must be PREPENDED before the EWM Protocol block. ``_build_pending_inbox_lead``
polls the mailbox (best-effort, bounded 6s) and renders a "handle these FIRST"
block. It MUST fail open to "" on any error — a poll hiccup can never delay or
break SessionStart.
"""

from __future__ import annotations

import importlib.util as _ilu
import json
import subprocess
import sys
from pathlib import Path


def _load_hook():
    hook_path = Path(__file__).resolve().parents[1] / (
        "empirica/plugins/claude-code-integration/hooks/ewm-protocol-loader.py"
    )
    spec = _ilu.spec_from_file_location("ewm_protocol_loader_hook", hook_path)
    assert spec and spec.loader, "could not load hook spec"
    mod = _ilu.module_from_spec(spec)
    sys.modules["ewm_protocol_loader_hook"] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_run(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _poll_json(proposals):
    return json.dumps({"ok": True, "ai_id": "empirica", "count": len(proposals), "proposals": proposals})


def _prop(i):
    return {
        "id": f"prop_{i:02d}",
        "source_claude": "empirica.david.ecodex",
        "status": "accepted",
        "type": "collab_brief",
        "title": f"Message {i}",
        "summary": f"summary body {i}",
    }


def test_renders_pending_messages(monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: "empirica")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(_poll_json([_prop(1)])))
    out = mod._build_pending_inbox_lead()
    assert "Pending mesh messages (1)" in out
    assert "handle these FIRST" in out
    assert "prop_01" in out
    assert "empirica.david.ecodex" in out
    assert "Message 1" in out
    assert "summary body 1" in out
    # leads before nothing here, but must carry the react-protocol footer
    assert "empirica mailbox reply" in out


def test_full_length_proposal_id_is_not_truncated(monkeypatch):
    # Regression (#267): real ids are "prop_" + 26 chars (31 total). A [:26] slice
    # dropped the last 5, so the surfaced id failed the `mailbox show/reply` commands
    # the block tells you to run. Prior fixtures used short ids (prop_01) and missed it.
    mod = _load_hook()
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: "empirica")
    full_id = "prop_6g73eoci7ngz3kcvd7aw6ktdqu"  # 31 chars
    assert len(full_id) == 31
    p = _prop(1)
    p["id"] = full_id
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(_poll_json([p])))
    out = mod._build_pending_inbox_lead()
    assert full_id in out  # complete id, copyable into `mailbox reply --parent-id`


def test_caps_at_eight_with_overflow_pointer(monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: "empirica")
    proposals = [_prop(i) for i in range(1, 21)]  # 20 pending
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(_poll_json(proposals)))
    out = mod._build_pending_inbox_lead()
    assert "Pending mesh messages (20)" in out
    assert "prop_08" in out  # 8th shown
    assert "prop_09" not in out  # 9th elided
    assert "and 12 more" in out


def test_truncates_long_summaries(monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: "empirica")
    p = _prop(1)
    p["summary"] = "x" * 500
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(_poll_json([p])))
    out = mod._build_pending_inbox_lead()
    assert "…" in out
    assert "x" * 500 not in out  # not the full 500-char body


def test_empty_inbox_returns_blank(monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: "empirica")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run(_poll_json([])))
    assert mod._build_pending_inbox_lead() == ""


def test_cli_nonzero_returns_blank(monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: "empirica")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run("boom", returncode=1))
    assert mod._build_pending_inbox_lead() == ""


def test_timeout_is_fail_open(monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: "empirica")

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="empirica", timeout=6)

    monkeypatch.setattr(subprocess, "run", _boom)
    assert mod._build_pending_inbox_lead() == ""  # a slow poll never breaks SessionStart


def test_missing_cli_is_fail_open(monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: "empirica")

    def _boom(*a, **k):
        raise FileNotFoundError("empirica not on PATH")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert mod._build_pending_inbox_lead() == ""


def test_no_ai_id_skips_poll(monkeypatch):
    mod = _load_hook()
    monkeypatch.setattr(mod, "_resolve_ai_id_for_poll", lambda: None)

    called = {"ran": False}

    def _track(*a, **k):
        called["ran"] = True
        return _fake_run(_poll_json([_prop(1)]))

    monkeypatch.setattr(subprocess, "run", _track)
    assert mod._build_pending_inbox_lead() == ""
    assert called["ran"] is False  # no ai_id → never even polls


def test_resolve_ai_id_reads_project_yaml(monkeypatch, tmp_path):
    mod = _load_hook()
    empdir = tmp_path / ".empirica"
    empdir.mkdir()
    (empdir / "project.yaml").write_text("ai_id: empirica\ncanonical_seat: empirica.david.empirica\n")
    monkeypatch.setattr(mod.Path, "cwd", staticmethod(lambda: tmp_path))
    # Force the git-root probe to fail so it falls through to cwd.
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _fake_run("", returncode=128))
    assert mod._resolve_ai_id_for_poll() == "empirica"  # basename preferred

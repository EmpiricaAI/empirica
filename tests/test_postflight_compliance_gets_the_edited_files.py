"""Goal-scoped compliance at POSTFLIGHT is scoped to the files the transaction edited.

`_run_postflight_compliance` read them through `R.hook_counters_read()`, behind a
hasattr guard. No such method exists, so the guard was always False and it fell
back to the transaction file, which has not carried `edited_files` since the
counters were split out. The counters were also deleted, by the close step,
before compliance ran. Every goal-scoped check therefore ran unscoped: the tests
check ran the full suite under a 300s timeout, and lint ran the whole repository.
"""

from __future__ import annotations

import json

from empirica.cli.command_handlers import _workflow_postflight as pf


def test_the_close_step_captures_edited_files_before_deleting_the_counters(tmp_path, monkeypatch):
    emp = tmp_path / ".empirica"
    emp.mkdir()
    (emp / "active_transaction_s.json").write_text(json.dumps({"status": "open", "transaction_id": "tx"}))
    (emp / "hook_counters_s.json").write_text(json.dumps({"tool_call_count": 3, "edited_files": ["a.py"]}))
    monkeypatch.setattr(pf.R, "transaction_write", lambda **_k: None)
    monkeypatch.setattr(pf.R, "transaction_read", lambda *_a, **_k: {})
    monkeypatch.setattr(pf.R, "counters_clear", lambda *_a, **_k: None)

    result: dict = {}
    pf._postflight_close_and_capture_counters(result, str(tmp_path), "_s")

    assert result["edited_files"] == ["a.py"]
    assert not (emp / "hook_counters_s.json").exists(), "the file read is the file cleared"


def test_compliance_receives_the_captured_files(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_checks(**kwargs):
        seen.update(kwargs)
        return None

    import empirica.core.post_test.compliance_loop as loop
    from empirica.config.service_registry import ServiceRegistry

    monkeypatch.setattr(loop, "run_compliance_checks", fake_checks)
    monkeypatch.setattr(ServiceRegistry, "list_all", staticmethod(lambda: ["x"]))
    monkeypatch.setattr(pf.R, "transaction_read", lambda *_a, **_k: {"domain": "code"})

    pf._run_postflight_compliance("sess", "tx", "code", str(tmp_path), ["a.py", "b.py"])

    assert seen["changed_files"] == ["a.py", "b.py"]


def test_without_captured_files_the_transaction_file_is_still_consulted(tmp_path, monkeypatch):
    """Positive control on the fallback, so the old source is not silently dropped."""
    seen: dict = {}

    import empirica.core.post_test.compliance_loop as loop
    from empirica.config.service_registry import ServiceRegistry

    monkeypatch.setattr(loop, "run_compliance_checks", lambda **kw: seen.update(kw))
    monkeypatch.setattr(ServiceRegistry, "list_all", staticmethod(lambda: ["x"]))
    monkeypatch.setattr(pf.R, "transaction_read", lambda *_a, **_k: {"domain": "code", "edited_files": ["c.py"]})

    pf._run_postflight_compliance("sess", "tx", "code", str(tmp_path))

    assert seen["changed_files"] == ["c.py"]

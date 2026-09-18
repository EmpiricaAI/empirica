"""A failed orphan-transaction adoption is reported on the session banner.

_adopt_orphaned_transaction copies the transaction file to the new instance
suffix and unlinks the old one. That move was wrapped in `except: pass`, and
the function returned the adopted session id regardless - so the banner said
"Adopted" while the transaction still sat under the old suffix and the new
instance could not find it at CHECK or POSTFLIGHT. Partial success as success.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

HOOK = (
    Path(__file__).resolve().parents[1]
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "session-init.py"
)


@pytest.fixture
def session_init():
    spec = importlib.util.spec_from_file_location("session_init_adoption_test", HOOK)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _orphan(tmp_path):
    empirica_dir = tmp_path / ".empirica"
    empirica_dir.mkdir()
    old = empirica_dir / "active_transaction_tmux_99.json"
    old.write_text(json.dumps({"transaction_id": "tx-orphan-1234", "session_id": "sess-1", "status": "open"}))
    return empirica_dir, old


def test_a_successful_adoption_moves_the_file_and_says_adopted(session_init, tmp_path, monkeypatch, capsys):
    empirica_dir, old = _orphan(tmp_path)
    import project_resolver

    monkeypatch.setattr(project_resolver, "_get_instance_suffix", lambda: "_tmux_1")

    out = session_init._adopt_orphaned_transaction(tmp_path)

    assert out == {"session_id": "sess-1", "source": "orphaned_transaction"}
    assert (empirica_dir / "active_transaction_tmux_1.json").exists()
    assert not old.exists()
    err = capsys.readouterr().err
    assert "Adopted orphaned transaction" in err
    assert "FAILED" not in err


def test_a_failed_move_is_reported_not_swallowed(session_init, tmp_path, monkeypatch, capsys):
    _empirica_dir, old = _orphan(tmp_path)
    import project_resolver

    monkeypatch.setattr(project_resolver, "_get_instance_suffix", lambda: "_tmux_1")

    def boom(*a, **k):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(session_init.shutil, "copy2", boom)

    out = session_init._adopt_orphaned_transaction(tmp_path)

    assert out["session_id"] == "sess-1"  # still best-effort, as before
    assert old.exists()  # the transaction stayed where it was
    err = capsys.readouterr().err
    assert "adoption of that transaction FAILED" in err
    assert "read-only filesystem" in err
    assert "active_transaction_tmux_99.json" in err

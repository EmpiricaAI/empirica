"""SessionStart adopts a transaction only when nobody alive owns it.

`_adopt_orphaned_transaction` took the newest OPEN transaction file in the project and re-keyed it (copy, then unlink)
to the new instance, whichever instance owned it. A sibling claude still running in the same project lost its
transaction to whichever session started next. The owner is knowable: Claude Code keeps ~/.claude/sessions/<pid>.json
for every running session (lib/instance_clash.live_claude_pid), and a transaction records its claude_session_id.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture
def init(monkeypatch):
    sys.path.insert(0, str(HOOKS.parent / "lib"))
    spec = importlib.util.spec_from_file_location("session_init_adoption", HOOKS / "session-init.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import project_resolver

    monkeypatch.setattr(project_resolver, "_get_instance_suffix", lambda: "_new")
    return module


def _tx(directory, name, claude_session_id, age_s, status="open"):
    path = directory / f"active_transaction_{name}.json"
    path.write_text(
        json.dumps(
            {
                "status": status,
                "session_id": f"empirica-{name}",
                "transaction_id": f"tx-{name}",
                "claude_session_id": claude_session_id,
            }
        )
    )
    stamp = time.time() - age_s
    os.utime(path, (stamp, stamp))
    return path


def _project(tmp_path):
    root = tmp_path / "proj"
    (root / ".empirica").mkdir(parents=True)
    return root, root / ".empirica"


def _live(monkeypatch, init, *live_ids):
    monkeypatch.setattr(init, "live_claude_pid", lambda csid: 4242 if csid in live_ids else None)


def test_a_live_siblings_transaction_is_not_taken(init, tmp_path, monkeypatch):
    root, directory = _project(tmp_path)
    sibling = _tx(directory, "sibling", "claude-sibling", age_s=1)
    _live(monkeypatch, init, "claude-sibling")
    assert init._adopt_orphaned_transaction(root, "claude-me") == {}
    assert sibling.exists() and not (directory / "active_transaction_new.json").exists()


def test_a_dead_owners_transaction_is_adopted_even_when_a_live_one_is_newer(init, tmp_path, monkeypatch):
    root, directory = _project(tmp_path)
    _tx(directory, "sibling", "claude-sibling", age_s=1)
    orphan = _tx(directory, "orphan", "claude-gone", age_s=500)
    _live(monkeypatch, init, "claude-sibling")
    found = init._adopt_orphaned_transaction(root, "claude-me")
    assert found == {"session_id": "empirica-orphan", "source": "orphaned_transaction"}
    assert not orphan.exists() and (directory / "active_transaction_new.json").exists()
    assert (directory / "active_transaction_sibling.json").exists()


def test_this_sessions_own_transaction_is_adopted_even_though_it_is_live(init, tmp_path, monkeypatch):
    """A resume: the owner IS this claude, so it is alive by definition and must still be taken back."""
    root, directory = _project(tmp_path)
    _tx(directory, "mine", "claude-me", age_s=5)
    _live(monkeypatch, init, "claude-me")
    assert init._adopt_orphaned_transaction(root, "claude-me")["session_id"] == "empirica-mine"


def test_a_transaction_with_no_recorded_owner_is_still_adopted(init, tmp_path, monkeypatch):
    root, directory = _project(tmp_path)
    _tx(directory, "legacy", None, age_s=5)
    _live(monkeypatch, init)
    assert init._adopt_orphaned_transaction(root, "claude-me")["session_id"] == "empirica-legacy"


def test_closed_transactions_are_never_adopted(init, tmp_path, monkeypatch):
    root, directory = _project(tmp_path)
    _tx(directory, "done", "claude-gone", age_s=5, status="closed")
    _live(monkeypatch, init)
    assert init._adopt_orphaned_transaction(root, "claude-me") == {}

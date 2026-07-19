"""Gate 5: cortex-mailbox-poll must NOT be auto-queued when a persistent
listener is armed for this ai_id — the listener already bridges those events.

Regression for the ai_id/session-UUID key mismatch (cortex prop_osuft3rn): the
listener_armed check must glob by AI_ID (``listener_active_<ai_id>_*.json``), not
the session ``instance_id``, or the gate never fires on wake-on-events seats and
the poller gets re-offered every session. Housekeeping crons (message-cleanup)
are not flagged redundant and still install.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

HOOK = (
    Path(__file__).parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "loop-install-pickup.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("loop_install_pickup", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    plugin_lib = HOOK.parent.parent / "lib"
    sys.path.insert(0, str(plugin_lib))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(plugin_lib))
    return mod


def _project(tmp_path, ai_id="test-ai"):
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text(yaml.safe_dump({"ai_id": ai_id}))
    return proj


def _setup(monkeypatch, tmp_path, listener_marker_for=None):
    mod = _load()
    home = tmp_path / "home"
    (home / ".empirica").mkdir(parents=True)
    if listener_marker_for:
        (home / ".empirica" / f"listener_active_{listener_marker_for}_x-inbox.json").write_text("{}")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    import empirica.core.cockpit.loop_install_request as lir
    import empirica.core.cockpit.loop_registry as lr

    monkeypatch.setattr(lr.LoopRegistry, "list_loops", lambda self: [])  # gate 3: empty registry
    queued: list[str] = []
    monkeypatch.setattr(lir, "write_pending", lambda **kw: queued.append(kw["name"]))
    return mod, queued


def test_poller_skipped_when_listener_armed_for_ai_id(tmp_path, monkeypatch):
    mod, queued = _setup(monkeypatch, tmp_path, listener_marker_for="test-ai")
    proj = _project(tmp_path, ai_id="test-ai")
    # instance_id is a SESSION UUID — deliberately different from the ai_id.
    mod._maybe_auto_install_canonical_loops("019f-session-uuid", proj)
    assert "cortex-mailbox-poll" not in queued  # gate 5 fired (redundant with listener)
    assert "message-cleanup" in queued  # housekeeping cron still installs


def test_poller_queued_when_no_listener(tmp_path, monkeypatch):
    mod, queued = _setup(monkeypatch, tmp_path, listener_marker_for=None)
    proj = _project(tmp_path, ai_id="test-ai")
    mod._maybe_auto_install_canonical_loops("019f-session-uuid", proj)
    assert "cortex-mailbox-poll" in queued  # non-wake-on-events → the poll is wanted
    assert "message-cleanup" in queued


def test_session_id_keyed_marker_does_not_arm_the_gate(tmp_path, monkeypatch):
    # The OLD buggy key: a marker keyed by the SESSION instance_id must NOT count
    # as armed — only the ai_id-keyed marker does. (This test fails on old code.)
    mod, queued = _setup(monkeypatch, tmp_path, listener_marker_for="019f-session-uuid")
    proj = _project(tmp_path, ai_id="test-ai")
    mod._maybe_auto_install_canonical_loops("019f-session-uuid", proj)
    assert "cortex-mailbox-poll" in queued  # session-id marker ≠ ai_id marker → not armed

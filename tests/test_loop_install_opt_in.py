"""cortex-mailbox-poll is OPT-IN ONLY — never auto-queued for install.

David 2026-07-19: wake-on-event (the persistent listener) is the canonical mesh
trigger; the 30s poller is only wanted on harnesses that can't do wake-on-event,
where the user opts in explicitly. The loop-install pickup hook must skip
`opt_in_only` loops entirely; genuine housekeeping crons (message-cleanup) still
auto-install. (cortex prop_osuft3rn; supersedes the earlier listener-armed gate.)
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


def _setup(monkeypatch, tmp_path):
    mod = _load()
    home = tmp_path / "home"
    (home / ".empirica").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

    import empirica.core.cockpit.loop_install_request as lir
    import empirica.core.cockpit.loop_registry as lr

    monkeypatch.setattr(lr.LoopRegistry, "list_loops", lambda self: [])  # empty registry (gate 3)
    queued: list[str] = []
    monkeypatch.setattr(lir, "write_pending", lambda **kw: queued.append(kw["name"]))
    return mod, queued


def test_opt_in_loop_never_auto_queued(tmp_path, monkeypatch):
    mod, queued = _setup(monkeypatch, tmp_path)
    proj = _project(tmp_path)
    mod._maybe_auto_install_canonical_loops("019f-session-uuid", proj)
    assert "cortex-mailbox-poll" not in queued  # opt-in only — never auto-queued
    assert "message-cleanup" in queued  # housekeeping cron still auto-installs


def test_no_listener_dependency(tmp_path, monkeypatch):
    # Even with NO listener marker present, the opt-in poller stays out of the
    # auto-queue — the old (superseded) code would have queued it here.
    mod, queued = _setup(monkeypatch, tmp_path)
    proj = _project(tmp_path)
    mod._maybe_auto_install_canonical_loops("any-instance", proj)
    assert "cortex-mailbox-poll" not in queued
    assert "message-cleanup" in queued


def test_catalog_flags():
    from empirica.core.cockpit.canonical_loops import CANONICAL_LOOPS

    by_name = {e["name"]: e for e in CANONICAL_LOOPS}
    assert by_name["cortex-mailbox-poll"].get("opt_in_only") is True
    assert not by_name["message-cleanup"].get("opt_in_only")

"""The canonical-loop auto-install cascade is retired, and pending install requests stay with their own instance (goal d02fcc9c).

``maybe_queue_canonical_install`` ran on every UserPromptSubmit (loop-install-pickup) and every SessionStart (session-init) to
queue the canonical loops for install. Every entry in ``CANONICAL_LOOPS`` is opt-in (the wake-on-event listener is the mesh
trigger; cron is never installed by default), so it could queue nothing, yet it still resolved the practice, probed the registry
and wrote a stamp file on each prompt. The cockpit's own install requests (``write_pending`` -> ``consume_pending``) are a
different, live path and must keep working.

Separately, ``list_pending`` matched files with ``loop_install_pending_<instance>_*.json``. An instance id that is a prefix of
another's at an underscore (``seat`` / ``seat_2``) therefore picked up, and ``consume_pending`` DELETED, the other instance's
requests.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

import empirica.core.cockpit.canonical_loops as cl
import empirica.core.cockpit.loop_install_request as lir

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


def _hook(name: str):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), HOOKS / f"{name}.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    lib = HOOKS.parent / "lib"
    sys.path.insert(0, str(lib))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(lib))
    return mod


@pytest.fixture
def home(tmp_path, monkeypatch):
    d = tmp_path / ".empirica"
    d.mkdir()
    monkeypatch.setattr(lir, "EMPIRICA_DIR", d)
    return d


# --- the cascade is gone -----------------------------------------------------------------------------------------------------


def test_the_cascade_function_no_longer_exists():
    assert not hasattr(cl, "maybe_queue_canonical_install")
    assert "maybe_queue_canonical_install" not in cl.__all__


@pytest.mark.parametrize("hook", ["session-init", "loop-install-pickup"])
def test_neither_hook_calls_it(hook):
    source = (HOOKS / f"{hook}.py").read_text()
    assert "maybe_queue_canonical_install" not in source
    assert "_maybe_auto_install_canonical_loops" not in source


def test_every_catalogue_entry_is_opt_in_so_nothing_installs_unasked():
    """The catalogue feeds the cockpit's fallback list; no code installs from it by itself. Each entry says so."""
    assert cl.CANONICAL_LOOPS, "control: there are entries to check"
    assert [e["name"] for e in cl.CANONICAL_LOOPS if not e.get("opt_in_only")] == []


def test_the_catalogue_is_still_readable_by_name():
    """Control: retiring the cascade must not take the catalogue (cockpit, parsers) with it."""
    assert cl.canonical_loop_by_name("cortex-mailbox-poll") is not None
    assert "message-cleanup" in cl.canonical_loop_names()


# --- the live path survives --------------------------------------------------------------------------------------------------


def test_the_pickup_hook_still_surfaces_a_cockpit_request(home, monkeypatch, capsys):
    hook = _hook("loop-install-pickup")
    monkeypatch.setattr(hook.InstanceResolver, "instance_id", classmethod(lambda cls: "seat-1"))
    lir.write_pending("seat-1", "metrics-watch", interval="15m", description="poll")
    assert hook.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert "metrics-watch" in out["hookSpecificOutput"]["additionalContext"]
    assert not list(home.glob("canonical_loops_installed_*")), "and it writes no stamp file"


def test_the_pickup_hook_with_nothing_pending_says_nothing(home, monkeypatch, capsys):
    hook = _hook("loop-install-pickup")
    monkeypatch.setattr(hook.InstanceResolver, "instance_id", classmethod(lambda cls: "seat-1"))
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {}
    assert list(home.iterdir()) == []


# --- pending requests belong to their instance -------------------------------------------------------------------------------


def test_an_instance_whose_id_prefixes_another_does_not_see_its_requests(home):
    lir.write_pending("seat_2", "job-b", interval="15m")
    assert lir.list_pending("seat") == [], "seat_2's request must not be listed under seat"
    assert [p.name for p in lir.list_pending("seat_2")] == ["loop_install_pending_seat_2_job-b.json"]


def test_consuming_one_instances_requests_leaves_the_other_untouched(home):
    lir.write_pending("seat", "job-a", interval="15m")
    lir.write_pending("seat_2", "job-b", interval="15m")
    assert [r.name for r in lir.consume_pending("seat")] == ["job-a"]
    assert [p.name for p in lir.list_pending("seat_2")] == ["loop_install_pending_seat_2_job-b.json"], "still pending"


def test_an_instance_still_sees_all_of_its_own_requests(home):
    lir.write_pending("seat", "a", interval="15m")
    lir.write_pending("seat", "b_with_underscore", interval="15m")
    assert sorted(r.name for r in lir.consume_pending("seat")) == ["a", "b_with_underscore"]


def test_an_unreadable_request_is_still_consumed_not_stranded(home):
    """Control for the filter: a file whose payload cannot be read has no instance to compare, so it keeps the old
    glob-match behaviour (reported and removed) rather than sitting there forever."""
    (home / "loop_install_pending_seat_broken.json").write_text("{not json")
    assert lir.consume_pending("seat") == []
    assert not (home / "loop_install_pending_seat_broken.json").exists()


# --- the same ambiguity in the three sibling pending-file stores ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("module", "writer_kwargs"),
    [
        ("listener_install_request", {"topic": "t"}),
        ("loop_uninstall_request", {"job_id": "j"}),
        ("listener_uninstall_request", {"monitor_task_id": "m"}),
    ],
)
def test_the_sibling_stores_do_not_hand_one_instance_anothers_requests(home, monkeypatch, module, writer_kwargs):
    import importlib

    mod = importlib.import_module(f"empirica.core.cockpit.{module}")
    monkeypatch.setattr(mod, "EMPIRICA_DIR", home)
    write = mod.write_pending
    write("seat", "job-a", **writer_kwargs)
    write("seat_2", "job-b", **writer_kwargs)
    assert [p.name for p in mod.list_pending("seat")] == [p.name for p in home.glob("*seat_job-a.json")]
    assert len(mod.list_pending("seat_2")) == 1
    assert [r.name for r in mod.consume_pending("seat")] == ["job-a"]
    assert len(mod.list_pending("seat_2")) == 1, "the other instance's request survived"


def test_the_migration_script_rewrites_the_payload_instance_too(tmp_path):
    """A renamed file whose payload still names the old instance is invisible to list_pending."""

    script = Path(__file__).resolve().parent.parent / "scripts" / "cockpit" / "migrate-tmux-to-slot.sh"
    assert 'sed -i "s/\\"instance_id\\"' in script.read_text(), "the script must rewrite the payload"

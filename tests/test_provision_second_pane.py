"""A second harness in the same practice is a second pane in that practice's window, not a practice of its own.

docs/guides/COCKPIT.md shows the layout: two projects at the same path, one launching claude and one ecodex, both panes of one
window, each with its own identity. provision-practice could only write the first kind: a second call with --launch ecodex made a
new directory and a new window. `--second-pane CMD` writes the second entry beside the first, so the onboarding interview can lay
out a person who runs both harnesses without a hand edit.

Everything is built under tmp_path with HOME pinned; `_run` is stubbed where a real `empirica` subprocess would run.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from empirica.cli.cli_core import create_argument_parser  # noqa: E402
from empirica.cli.command_handlers import provision_practice_commands as pp  # noqa: E402
from empirica.core.cockpit.launcher.config import (  # noqa: E402
    ProfileError,
    add_practice_to_profile,
    load_config,
    profile_path,
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    elsewhere = tmp_path / "elsewhere"  # a cwd with no project.yaml, so nothing is inferred
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return h


@pytest.fixture
def stub_run(monkeypatch):
    """`project-init` leaves a project file behind, as the real one does; every other command succeeds."""

    def fake_run(cmd, cwd, dry_run):
        if cmd[:2] == ["empirica", "project-init"] and not dry_run:
            (Path(cwd) / ".empirica").mkdir(exist_ok=True)
            (Path(cwd) / ".empirica" / "project.yaml").write_text("project_id: x\nname: p\n")
        return True, ""

    monkeypatch.setattr(pp, "_run", fake_run)


def _args(tmp_path, **kw):
    base = {
        "name": "p",
        "base_path": str(tmp_path / "base"),
        "tenant": "t",
        "org": "o",
        "no_cortex": True,
        "substrate": None,
        "cockpit_profile": None,
        "launch": None,
        "resume": None,
        "second_pane": None,
        "forgejo_owner": None,
        "forgejo_host": None,
        "dry_run": False,
        "output": "json",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _group(config, name):
    return next(g for g in config.groups if g.name == name)


# ── the profile writer ────────────────────────────────────────────────────────────────────────────────────────────────────


def test_beside_puts_the_new_project_in_the_first_ones_window(tmp_path):
    path = str(tmp_path / "research")
    add_practice_to_profile("dev", "research", path)
    target, changed = add_practice_to_profile("dev", "research-ecodex", path, launch="ecodex", beside="research")

    assert changed is True
    config = load_config(target)
    assert [g.name for g in config.groups] == ["monitor", "research"]  # no third window
    assert [pane.project_ref for pane in _group(config, "research").panes] == ["research", "research-ecodex"]
    second = config.project_by_name("research-ecodex")
    assert second is not None and second.launch == "ecodex"
    assert Path(second.path) == Path(path)


def test_running_it_again_changes_nothing(tmp_path):
    path = str(tmp_path / "research")
    add_practice_to_profile("dev", "research", path)
    add_practice_to_profile("dev", "research-ecodex", path, launch="ecodex", beside="research")
    before = profile_path("dev").read_text()

    _, changed = add_practice_to_profile("dev", "research-ecodex", path, launch="ecodex", beside="research")

    assert changed is False
    assert profile_path("dev").read_text() == before


def test_beside_a_project_with_no_window_is_refused_and_nothing_is_written(tmp_path):
    add_practice_to_profile("dev", "research", str(tmp_path / "research"))
    before = profile_path("dev").read_text()

    with pytest.raises(ProfileError, match="no window"):
        add_practice_to_profile("dev", "other-ecodex", str(tmp_path / "other"), launch="ecodex", beside="other")
    assert profile_path("dev").read_text() == before


# ── provision-practice --second-pane ──────────────────────────────────────────────────────────────────────────────────────


def test_second_pane_adds_the_harness_beside_the_practice(tmp_path, stub_run, capsys):
    rc = pp.handle_provision_practice_command(_args(tmp_path, cockpit_profile="dev", second_pane="ecodex"))

    assert rc == 0
    steps = {s["step"]: s for s in json.loads(capsys.readouterr().out)["steps"]}
    assert steps["cockpit-second-pane"]["changed"] is True
    config = load_config(profile_path("dev"))
    assert [pane.project_ref for pane in _group(config, "p").panes] == ["p", "p-ecodex"]
    second = config.project_by_name("p-ecodex")
    assert second is not None and second.launch == "ecodex"
    assert Path(second.path) == tmp_path / "base" / "p"  # the same directory, not a new practice
    assert not (tmp_path / "base" / "p-ecodex").exists()


def test_second_pane_needs_a_cockpit_profile(tmp_path, stub_run, capsys):
    rc = pp.handle_provision_practice_command(_args(tmp_path, second_pane="ecodex"))

    assert rc == 1
    assert "--cockpit-profile" in json.loads(capsys.readouterr().err)["error"]
    assert not (tmp_path / "base" / "p").exists()


def test_the_same_harness_twice_is_refused(tmp_path, stub_run, capsys):
    rc = pp.handle_provision_practice_command(
        _args(tmp_path, cockpit_profile="dev", launch="ecodex", second_pane="ecodex")
    )

    assert rc == 1
    assert "ecodex" in json.loads(capsys.readouterr().err)["error"]
    assert not (tmp_path / "base" / "p").exists()


def test_a_dry_run_says_what_it_would_add_and_writes_nothing(tmp_path, stub_run, capsys):
    rc = pp.handle_provision_practice_command(
        _args(tmp_path, cockpit_profile="dev", second_pane="ecodex", dry_run=True)
    )

    assert rc == 0
    steps = {s["step"]: s for s in json.loads(capsys.readouterr().out)["steps"]}
    assert steps["cockpit-second-pane"]["changed"] is True
    assert "dry-run" in steps["cockpit-second-pane"]["note"]
    assert not profile_path("dev").exists()


def test_the_parser_takes_second_pane():
    ns = create_argument_parser().parse_args(
        ["provision-practice", "p", "--cockpit-profile", "dev", "--second-pane", "ecodex"]
    )

    assert ns.second_pane == "ecodex"


def test_a_second_pane_in_a_profile_without_windows_is_refused_before_anything_is_written(tmp_path, stub_run, capsys):
    """A projects-only profile opens one window per project and has no window to put a second pane in. The main entry was
    written and then the step failed, with advice to add what was already there (pre-publish sweep, 1.14.22)."""
    target = profile_path("dev")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("session_name: dev\nprojects:\n  - name: other\n    path: /tmp/other\n")
    before = target.read_text()

    for dry_run in (True, False):
        rc = pp.handle_provision_practice_command(
            _args(tmp_path, cockpit_profile="dev", second_pane="ecodex", dry_run=dry_run)
        )
        out = json.loads(capsys.readouterr().out)
        steps = {s["step"]: s for s in out["steps"]}
        assert rc == 1, dry_run
        assert "windows" in steps["cockpit-profile"]["error"]
        assert "cockpit-second-pane" not in steps
    assert target.read_text() == before

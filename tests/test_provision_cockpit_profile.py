"""Onboarding ends with a cockpit profile the user can launch.

David, 2026-09-26: the cockpit is invisible to people. After the EWM interview
provisions their practices, it should have written the cockpit for them and told
them the one command that opens it. `provision-practice --cockpit-profile NAME`
does that, one practice per call, all into the same profile.

Everything is built under tmp_path: HOME is pinned so nothing touches ~/.empirica.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from empirica.core.cockpit.launcher.config import (
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
    return h


def test_a_new_profile_gets_a_tui_window_then_one_window_per_practice(tmp_path):
    f, changed = add_practice_to_profile("me", "research", str(tmp_path / "research"))

    assert changed and f == profile_path("me")
    cfg = load_config(f)
    assert cfg.session_name == "cockpit-me" and cfg.surface == "tmux"
    assert [g.name for g in cfg.groups] == ["monitor", "research"]
    assert cfg.groups[0].panes[0].inline_command == "empirica tui"
    assert cfg.project_by_name("research").launch == "claude"
    assert "empirica cockpit launch --profile me" in f.read_text(), "the file says how to launch it"


def test_a_second_practice_is_added_and_a_repeat_changes_nothing(tmp_path):
    add_practice_to_profile("me", "a", str(tmp_path / "a"))
    f, changed_b = add_practice_to_profile("me", "b", str(tmp_path / "b"))
    before = f.read_text()
    _, changed_again = add_practice_to_profile("me", "b", str(tmp_path / "b"))

    assert changed_b and not changed_again
    assert f.read_text() == before, "idempotent: a re-run must not rewrite the file"
    assert [g.name for g in load_config(f).groups] == ["monitor", "a", "b"]


def test_an_edited_profile_is_backed_up_before_it_is_rewritten(tmp_path):
    f, _ = add_practice_to_profile("me", "a", str(tmp_path / "a"))
    f.write_text(f.read_text() + "# my note\n")

    add_practice_to_profile("me", "b", str(tmp_path / "b"))

    assert "# my note" in f.with_suffix(".yaml.bak").read_text()


@pytest.mark.parametrize("content", ["session_name: [unclosed", "- just\n- a list\n", "42"])
def test_an_unreadable_profile_is_refused_and_left_exactly_as_it_was(tmp_path, content):
    """load_config returns defaults for these, so a naive load-modify-write would
    silently replace the user's profile with a fresh one."""
    f = profile_path("me")
    f.parent.mkdir(parents=True)
    f.write_text(content)

    with pytest.raises(ProfileError, match="nothing was changed"):
        add_practice_to_profile("me", "a", str(tmp_path / "a"))

    assert f.read_text() == content


@pytest.mark.parametrize("bad", ["../evil", "a/b", ".hidden", "-x", "", "UPPER", "a b"])
def test_a_profile_name_cannot_escape_the_cockpit_directory(bad):
    with pytest.raises(ProfileError):
        profile_path(bad)


# ── the verb ────────────────────────────────────────────────────────────────


def _run_verb(monkeypatch, tmp_path, capsys, *extra, dry_run=False, project_init_ok=True):
    from empirica.cli.command_handlers import provision_practice_commands as ppc
    from empirica.cli.parsers import provision_practice_parsers as pp

    def fake_run(cmd, cwd, dry):
        if cmd[1] == "project-init":
            if not project_init_ok:
                return False, "boom"
            (cwd / ".empirica").mkdir(parents=True, exist_ok=True)
            (cwd / ".empirica" / "project.yaml").write_text("ai_id: x\n")
        return True, ""

    monkeypatch.setattr(ppc, "_run", fake_run)
    import argparse

    ap = argparse.ArgumentParser()
    pp.add_provision_practice_parsers(ap.add_subparsers())
    argv = ["provision-practice", "research", "--base-path", str(tmp_path / "base"), "--tenant", "t", "--org", "o"]
    argv += ["--output", "json", *extra, *(["--dry-run"] if dry_run else [])]
    rc = ppc.handle_provision_practice_command(ap.parse_args(argv))
    out = capsys.readouterr().out
    # dry-run mode prints progress lines ahead of the JSON document; parse the document.
    start = out.rfind("\n{\n") + 1 if "\n{\n" in out else 0
    return rc, json.loads(out[start:])


def test_provisioning_with_a_profile_writes_it_and_reports_the_launch_command(monkeypatch, tmp_path, capsys):
    rc, out = _run_verb(monkeypatch, tmp_path, capsys, "--cockpit-profile", "me")

    assert rc == 0 and out["launch_command"] == "empirica cockpit launch --profile me"
    step = next(s for s in out["steps"] if s["step"] == "cockpit-profile")
    assert step["changed"] is True
    cfg = load_config(profile_path("me"))
    assert cfg.project_by_name("research").path == str(tmp_path / "base" / "research")


def test_dry_run_reports_the_step_but_writes_nothing(monkeypatch, tmp_path, capsys):
    rc, out = _run_verb(monkeypatch, tmp_path, capsys, "--cockpit-profile", "me", dry_run=True)

    assert rc == 0 and "dry-run" in next(s for s in out["steps"] if s["step"] == "cockpit-profile")["note"]
    assert not profile_path("me").exists()


def test_a_failed_provision_does_not_list_a_practice_that_never_came_up(monkeypatch, tmp_path, capsys):
    rc, _ = _run_verb(monkeypatch, tmp_path, capsys, "--cockpit-profile", "me", project_init_ok=False)

    assert rc == 1 and not profile_path("me").exists(), "an empty pane in the cockpit is worse than none"


def test_a_bad_profile_name_fails_the_step_not_the_whole_provision(monkeypatch, tmp_path, capsys):
    rc, out = _run_verb(monkeypatch, tmp_path, capsys, "--cockpit-profile", "../evil")

    step = next(s for s in out["steps"] if s["step"] == "cockpit-profile")
    assert rc == 1 and "must be lowercase" in step["error"]
    assert (tmp_path / "base" / "research" / ".empirica" / "project.yaml").exists(), (
        "the practice itself was provisioned"
    )


def test_without_the_flag_nothing_about_cockpits_appears(monkeypatch, tmp_path, capsys):
    rc, out = _run_verb(monkeypatch, tmp_path, capsys)

    assert rc == 0 and "launch_command" not in out
    assert all(s["step"] != "cockpit-profile" for s in out["steps"])
    assert not (Path.home() / ".empirica" / "cockpit").exists()


def test_the_written_profile_is_a_document_the_launcher_can_build(tmp_path):
    """The file must round-trip through the loader the launcher uses."""
    f, _ = add_practice_to_profile("me", "a", str(tmp_path / "a"))
    add_practice_to_profile("me", "b", str(tmp_path / "b"))

    raw = yaml.safe_load(f.read_text())
    cfg = load_config(f)
    assert raw["surface"] == "tmux" and cfg.is_groups_mode()
    assert [p.name for p in cfg.projects] == ["a", "b"]

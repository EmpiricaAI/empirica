"""PREFLIGHT refuses when the store it would write to is not the project the caller stands in.

2026-09-29 and 10-01: a claude in empirica-nle carrying the instance id `empirica` opened PREFLIGHTs
that landed in core's store. Every resolver routes by the instance pointer, not the working
directory, and the existing PREFLIGHT guard only refuses a session that is absent locally and owned by
another practice; the session used here existed in core. David approved refuse-with-override.

Everything is built under tmp_path with HOME pinned; the store resolver is injected.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from empirica.cli.command_handlers import _workflow_preflight as wp


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".empirica").mkdir(parents=True)  # the home .empirica is a trap: it is not a project
    monkeypatch.setenv("HOME", str(home))
    for var in ("EMPIRICA_ALLOW_PROJECT_MISMATCH", "EMPIRICA_SESSION_DB", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(var, raising=False)

    def project(name: str) -> Path:
        root = home / "work" / name
        (root / ".empirica" / "sessions").mkdir(parents=True)
        (root / ".empirica" / "project.yaml").write_text(f"ai_id: {name}\n")
        return root

    core, nle = project("core"), project("nle")
    store = core / ".empirica" / "sessions" / "sessions.db"
    monkeypatch.setattr("empirica.config.path_resolver.get_session_db_path", lambda: store)
    return types.SimpleNamespace(home=home, core=core, nle=nle, mp=monkeypatch)


def _record(home: Path, session_id: str, project: Path, source: str) -> None:
    (home / ".empirica" / f"active_work_{session_id}.json").write_text(
        json.dumps({"project_path": str(project), "source": source})
    )


def test_a_claude_standing_in_another_project_than_its_store_is_refused(world):
    """POSITIVE CONTROL: the real shape. nle's checkout, core's store."""
    world.mp.chdir(world.nle)

    out = wp._preflight_check_project_matches_cwd()

    assert out and out["refuse"] is True
    assert "'core'" in out["message"] and "'nle'" in out["message"]
    assert "EMPIRICA_INSTANCE_ID" in out["message"]
    assert "project-switch nle --claude-session-id" in out["fix"] and "EMPIRICA_ALLOW_PROJECT_MISMATCH=1" in out["fix"]


def test_standing_in_the_stores_own_project_never_refuses(world):
    """CONTROL: without it the test above passes for a guard that refuses everything."""
    world.mp.chdir(world.core)

    assert wp._preflight_check_project_matches_cwd() is None


def test_a_subdirectory_of_the_stores_project_is_the_same_project(world):
    deep = world.core / "src" / "deep"
    deep.mkdir(parents=True)
    world.mp.chdir(deep)

    assert wp._preflight_check_project_matches_cwd() is None


def test_a_working_directory_outside_any_project_never_refuses(world, tmp_path):
    elsewhere = tmp_path / "scratch"
    elsewhere.mkdir()
    world.mp.chdir(elsewhere)

    assert wp._preflight_check_project_matches_cwd() is None


def test_a_directory_under_home_is_not_owned_by_the_homes_dot_empirica(world):
    """The trap: ~/.empirica exists, so 'nearest .empirica' would call any directory under home a project."""
    plain = world.home / "notes"
    plain.mkdir()
    world.mp.chdir(plain)

    assert wp._project_root_of(plain.resolve()) is None
    assert wp._preflight_check_project_matches_cwd() is None


def test_this_sessions_own_deliberate_switch_is_allowed(world):
    _record(world.home, "sess-1", world.core, "project-switch")
    world.mp.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    world.mp.chdir(world.nle)

    assert wp._preflight_check_project_matches_cwd() is None


def test_another_sessions_switch_does_not_count(world):
    """The point of keying on the session: the pointer a different claude wrote is not my decision."""
    _record(world.home, "someone-else", world.core, "project-switch")
    world.mp.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    world.mp.chdir(world.nle)

    assert wp._preflight_check_project_matches_cwd() is not None


def test_a_record_not_written_by_project_switch_does_not_count(world):
    """A hook-written record (session-init, post-compact) is routing, not a deliberate choice."""
    _record(world.home, "sess-1", world.core, "post-compact")
    world.mp.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    world.mp.chdir(world.nle)

    assert wp._preflight_check_project_matches_cwd() is not None


def test_a_switch_to_a_different_store_than_the_one_resolved_does_not_count(world):
    _record(world.home, "sess-1", world.nle, "project-switch")
    world.mp.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    world.mp.chdir(world.nle)  # standing in nle; store is core; the switch named nle, not core

    assert wp._preflight_check_project_matches_cwd() is not None


def test_the_environment_override_lets_it_through(world):
    world.mp.setenv("EMPIRICA_ALLOW_PROJECT_MISMATCH", "1")
    world.mp.chdir(world.nle)

    assert wp._preflight_check_project_matches_cwd() is None


def test_an_explicit_session_db_is_a_stated_choice_and_is_left_alone(world):
    world.mp.setenv("EMPIRICA_SESSION_DB", "/anything.db")
    world.mp.chdir(world.nle)

    assert wp._preflight_check_project_matches_cwd() is None


def test_a_guard_that_fails_does_not_block_preflight_but_says_so(world):
    """A persistent failure must not look identical to a pass: it is returned, not swallowed."""

    def boom():
        raise RuntimeError("resolver down")

    world.mp.setattr("empirica.config.path_resolver.get_session_db_path", boom)
    world.mp.chdir(world.nle)

    out = wp._preflight_check_project_matches_cwd()

    assert out and not out.get("refuse") and "RuntimeError: resolver down" in out["skipped"]


def test_a_non_dict_active_work_record_is_not_a_crash_and_not_a_pass(world):
    (world.home / ".empirica" / "active_work_sess-1.json").write_text("[1, 2]")
    world.mp.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    world.mp.chdir(world.nle)

    out = wp._preflight_check_project_matches_cwd()

    assert out and out.get("refuse") is True


def _marker(home: Path, session_id: str, project: Path) -> None:
    (home / ".empirica" / f"deliberate_switch_{session_id}.json").write_text(json.dumps({"project_path": str(project)}))


def test_the_deliberate_switch_marker_survives_a_hook_rewriting_active_work(world):
    """post-compact and session-init overwrite active_work with their own source; the marker is theirs to leave alone."""
    _marker(world.home, "sess-1", world.core)
    _record(world.home, "sess-1", world.core, "post-compact")  # the hook rewrote the record
    world.mp.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    world.mp.chdir(world.nle)

    assert wp._preflight_check_project_matches_cwd() is None


def test_another_sessions_marker_does_not_count(world):
    _marker(world.home, "someone-else", world.core)
    world.mp.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    world.mp.chdir(world.nle)

    assert wp._preflight_check_project_matches_cwd() is not None


def test_a_marker_naming_a_different_store_does_not_count(world):
    _marker(world.home, "sess-1", world.nle)
    world.mp.setenv("CLAUDE_CODE_SESSION_ID", "sess-1")
    world.mp.chdir(world.nle)

    assert wp._preflight_check_project_matches_cwd() is not None


def _git(cwd: Path, *args: str) -> None:
    import subprocess

    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_a_linked_worktree_of_the_stores_repo_is_the_same_project(world, tmp_path):
    """A repo that TRACKS .empirica/project.yaml gives every worktree its own copy, which read as a different project."""
    main = tmp_path / "tracked"
    (main / ".empirica" / "sessions").mkdir(parents=True)
    (main / ".empirica" / "project.yaml").write_text("ai_id: tracked\n")
    _git(main, "init", "-q", "-b", "main")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "x")
    wt = tmp_path / "tracked-wt"
    _git(main, "worktree", "add", "-q", str(wt))
    world.mp.setattr(
        "empirica.config.path_resolver.get_session_db_path", lambda: main / ".empirica" / "sessions" / "sessions.db"
    )
    world.mp.chdir(wt)

    assert wp._project_root_of(wt.resolve()) == wt.resolve(), "the worktree does read as its own project root"
    assert wp._preflight_check_project_matches_cwd() is None


def test_a_different_repo_with_its_own_project_is_still_refused(world, tmp_path):
    """CONTROL: the worktree allowance must not wave through an unrelated repository."""
    a = tmp_path / "repo-a"
    b = tmp_path / "repo-b"
    for r in (a, b):
        (r / ".empirica" / "sessions").mkdir(parents=True)
        (r / ".empirica" / "project.yaml").write_text(f"ai_id: {r.name}\n")
        _git(r, "init", "-q", "-b", "main")
        _git(r, "add", "-A")
        _git(r, "commit", "-q", "-m", "x")
    world.mp.setattr(
        "empirica.config.path_resolver.get_session_db_path", lambda: a / ".empirica" / "sessions" / "sessions.db"
    )
    world.mp.chdir(b)

    out = wp._preflight_check_project_matches_cwd()

    assert out and out.get("refuse") is True


def test_project_switch_writes_the_marker_hooks_cannot_overwrite(world, tmp_path):
    from empirica.cli.command_handlers import project_commands as pc

    ok = pc._update_active_work(str(world.core), "core", None, "sess-9")

    assert ok
    marker = json.loads((world.home / ".empirica" / "deliberate_switch_sess-9.json").read_text())
    assert marker["project_path"] == str(world.core) and marker["claude_session_id"] == "sess-9"


# ── wiring: the call site must exist and must refuse before any write ───────


def test_the_handler_refuses_before_writing_when_the_guard_says_so(world, capsys):
    """Replace the call in the handler with `pass` and this fails: the earlier guard shipped with a
    blanket except and no test that the call was live."""
    wrote: list[str] = []
    parsed = {
        "session_id": "s",
        "vectors": {},
        "reasoning": "",
        "task_context": "",
        "output_format": "json",
    }
    world.mp.setattr(wp, "_preflight_parse_and_validate", lambda args: parsed)
    world.mp.setattr(wp, "_preflight_check_unclosed_transaction", lambda: None)
    world.mp.setattr(wp, "_preflight_check_session_exists", lambda sid: None)
    world.mp.setattr(
        wp,
        "_preflight_check_project_matches_cwd",
        lambda: {"refuse": True, "message": "wrong store", "fix": "do x"},
    )
    world.mp.setattr(wp, "_preflight_create_checkpoint", lambda *a, **k: wrote.append("checkpoint"))

    rc = wp.handle_preflight_submit_command(types.SimpleNamespace(schema=False, output="json"))

    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False and out["error"] == "wrong store" and out["fix"] == "do x"
    assert wrote == [], "refused means nothing was written"

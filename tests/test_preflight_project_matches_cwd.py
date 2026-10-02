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


def test_a_guard_that_fails_does_not_block_preflight(world):
    def boom():
        raise RuntimeError("resolver down")

    world.mp.setattr("empirica.config.path_resolver.get_session_db_path", boom)
    world.mp.chdir(world.nle)

    assert wp._preflight_check_project_matches_cwd() is None


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

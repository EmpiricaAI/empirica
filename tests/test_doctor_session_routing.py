"""doctor reports a live claude whose hooks resolve it in a different project than it runs in.

empirica-nle, 2026-09-29: `~/.empirica/active_work_<its claude session>.json` named core's project,
so every hook for that session looked in core's tree. Its stop hook then enforced core's open
transaction. Nothing reported it and the session could not repair it from inside, because the gate
the bad record causes refuses the repair.

The claude process does not carry its session id in its environment; Claude Code's own
`~/.claude/sessions/<pid>.json` is the way from a pid to a session. The logic is tested on
injected process lists; the file reader is tested on files built under tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

from empirica.cli.command_handlers.doctor import (
    PASS,
    SKIP,
    WARN,
    _claude_session_file,
    _empirica_project_root,
    _session_id_for_process,
    check_session_routing,
)


def _project(root: Path) -> Path:
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(f"ai_id: {root.name}\n")
    return root


def _record(home: Path, session_id: str, project: Path | str) -> None:
    d = home / ".empirica"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"active_work_{session_id}.json").write_text(json.dumps({"project_path": str(project)}))


def _p(pid: int, cwd: Path, session_id: str | None) -> dict:
    return {"pid": pid, "cwd": str(cwd), "session_id": session_id}


def test_a_session_routed_to_another_practice_is_flagged_with_the_repair(tmp_path):
    """POSITIVE CONTROL: the real shape. NLE's session, core's project in its record."""
    home = tmp_path / "home"
    core = _project(tmp_path / "empirica")
    nle = _project(tmp_path / "empirica-nle")
    _record(home, "sess-nle", core)

    c = check_session_routing([_p(7, nle, "sess-nle")], home)

    assert c.status == WARN
    assert c.data["mismatched"] == [{"pid": 7, "session_id": "sess-nle", "runs_in": str(nle), "routed_to": str(core)}]
    assert "pid 7 runs in empirica-nle but its hooks resolve it in empirica" in c.detail
    assert "project-switch empirica-nle --claude-session-id sess-nle" in c.hint
    assert "without it only the instance pointer changes" in c.hint


def test_a_session_routed_where_it_runs_passes_and_says_what_it_read(tmp_path):
    home = tmp_path / "home"
    a = _project(tmp_path / "a")
    _record(home, "s1", a)

    c = check_session_routing([_p(1, a, "s1")], home)

    assert c.status == PASS and "1 routed where they run" in c.detail


def test_a_subdirectory_resolves_to_its_project_root(tmp_path):
    home = tmp_path / "home"
    a = _project(tmp_path / "a")
    deep = a / "src" / "deep"
    deep.mkdir(parents=True)
    _record(home, "s1", a)

    assert check_session_routing([_p(1, deep, "s1")], home).status == PASS


def test_a_trailing_slash_or_symlink_is_the_same_project(tmp_path):
    home = tmp_path / "home"
    a = _project(tmp_path / "a")
    link = tmp_path / "link"
    link.symlink_to(a)
    _record(home, "s1", str(link) + "/")

    assert check_session_routing([_p(1, a, "s1")], home).status == PASS


def test_no_record_no_session_file_and_no_project_are_counted_not_passed_silently(tmp_path):
    """A PASS that skipped what it could not compare would overstate what was checked."""
    home = tmp_path / "home"
    a = _project(tmp_path / "a")
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    _record(home, "s3", a)

    c = check_session_routing([_p(1, a, "s1"), _p(2, a, None), _p(3, nowhere, "s3")], home)

    assert c.status == WARN, "nothing was compared, so it is not a pass"
    assert "1 with no record" in c.detail and "1 with no session file" in c.detail and "1 outside a project" in c.detail


def test_unreadable_processes_stop_a_clean_pass(tmp_path):
    a = _project(tmp_path / "a")
    home = tmp_path / "home"
    _record(home, "s1", a)

    c = check_session_routing([_p(1, a, "s1")], home, unreadable=2)

    assert c.status == WARN and "2 unreadable" in c.detail


def test_no_claude_at_all_is_a_skip():
    assert check_session_routing([], Path("/nonexistent")).status == SKIP


# ── the file reader ──────────────────────────────────────────────────────────


def test_the_session_file_reader_returns_the_session_id_and_tolerates_absence(tmp_path):
    claude = tmp_path / ".claude"
    (claude / "sessions").mkdir(parents=True)
    (claude / "sessions" / "42.json").write_text(json.dumps({"pid": 42, "sessionId": "abc", "cwd": "/x"}))
    (claude / "sessions" / "43.json").write_text("{not json")
    (claude / "sessions" / "44.json").write_text(json.dumps({"pid": 44}))

    assert _claude_session_file(42, claude)["sessionId"] == "abc"
    assert _claude_session_file(43, claude) is None, "malformed: not checked, never agreement"
    assert _claude_session_file(44, claude) is None, "no sessionId: unrecognised format"
    assert _claude_session_file(45, claude) is None, "no file"


# ── hardening from the 1.14.5 broccoli sweep ────────────────────────────────


def test_the_homes_dot_empirica_does_not_make_every_directory_under_home_a_project(tmp_path, monkeypatch):
    """~/.empirica exists for every empirica user, so 'nearest .empirica' called ~/Downloads part of the home."""
    home = tmp_path / "home"
    (home / ".empirica").mkdir(parents=True)
    plain = home / "Downloads"
    plain.mkdir()
    monkeypatch.setenv("HOME", str(home))

    assert _empirica_project_root(str(plain)) is None
    c = check_session_routing([_p(1, plain, "s1")], home)
    assert "1 outside a project" not in c.detail or c.status == WARN  # not read as running in the home


def test_a_nested_dot_empirica_without_a_project_file_is_not_a_project(tmp_path):
    repo = _project(tmp_path / "repo")
    nested = repo / "tests"
    (nested / ".empirica").mkdir(parents=True)

    assert _empirica_project_root(str(nested)) == repo, "tests/.empirica has no project.yaml: the repo is the project"


def test_a_non_string_project_path_is_a_mismatch_candidate_not_a_crash(tmp_path):
    """It raised TypeError out of os.path.realpath, and doctor has no per-check guard."""
    home = tmp_path / "home"
    a = _project(tmp_path / "a")
    (home / ".empirica").mkdir(parents=True)
    (home / ".empirica" / "active_work_s1.json").write_text(
        json.dumps({"project_path": 5})
    )  # raw: _record() stringifies

    c = check_session_routing([_p(1, a, "s1")], home)

    assert c.status == WARN and "1 outside a project" in c.detail


def test_nothing_compared_is_a_warning_not_a_pass(tmp_path):
    """If Claude Code renamed its session files every process would be unmapped and doctor would be green forever."""
    a = _project(tmp_path / "a")

    c = check_session_routing([_p(1, a, None), _p(2, a, None)], tmp_path / "home")

    assert c.status == WARN and "no session could be compared" in c.detail


def test_a_session_file_whose_cwd_is_not_the_processs_cwd_is_not_its_file(tmp_path):
    """pid reuse: the file belongs to a dead claude that had that pid."""
    claude = tmp_path / ".claude"
    (claude / "sessions").mkdir(parents=True)
    here, elsewhere = tmp_path / "here", tmp_path / "elsewhere"
    here.mkdir()
    elsewhere.mkdir()
    (claude / "sessions" / "42.json").write_text(json.dumps({"pid": 42, "sessionId": "abc", "cwd": str(elsewhere)}))

    assert _session_id_for_process({"pid": 42, "cwd": str(here)}, claude) is None
    assert _session_id_for_process({"pid": 42, "cwd": str(elsewhere)}, claude) == "abc", (
        "CONTROL: a matching cwd is accepted"
    )

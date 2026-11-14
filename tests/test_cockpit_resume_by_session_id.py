"""`cockpit refresh` resumes a claude pane by the session id it last ran, not the newest conversation in its directory (goal dfa8b484).

`--continue` picks the most recent conversation in the pane's directory. Two panes on one practice, or a headless `claude -p` run in that
directory, make that a different conversation from the one the pane was in. Each cockpit pane carries an instance id, and
`~/.empirica/instance_projects/<instance id>.json` records the Claude Code session it is running, so refresh can name it. The id is used only
when the record is for this pane's project and a transcript with that id exists and was started in that directory; anything else falls back
to `--continue`, which is what refresh did before.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from empirica.core.cockpit.launcher import resume_ids
from empirica.core.cockpit.launcher import tmux as T
from empirica.core.cockpit.launcher.config import GroupSpec, LauncherConfig, PaneSpec, ProjectSpec

SID = "0f3c9a1e-1111-4222-8333-444455556666"


@pytest.fixture
def home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".empirica" / "instance_projects").mkdir(parents=True)
    (home / ".claude" / "projects").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    proj = tmp_path / "practice"
    proj.mkdir()
    return home, proj


def _record(home: Path, iid: str, **data) -> None:
    (home / ".empirica" / "instance_projects" / f"{iid}.json").write_text(json.dumps(data))


def _transcript(home: Path, sid: str, cwd: str, folder: str = "-some-folder") -> Path:
    d = home / ".claude" / "projects" / folder
    d.mkdir(exist_ok=True)
    path = d / f"{sid}.jsonl"
    path.write_text(
        json.dumps({"type": "summary", "summary": "x"})
        + "\n"
        + json.dumps({"type": "user", "cwd": cwd, "sessionId": sid})
        + "\n"
    )
    return path


# ── the lookup ───────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_recorded_session_is_returned_when_every_guard_holds(home):
    h, proj = home
    _record(h, "core", project_path=str(proj), claude_session_id=SID)
    _transcript(h, SID, str(proj))

    assert resume_ids.last_session_id("core", str(proj)) == SID


@pytest.mark.parametrize(
    "why, setup",
    [
        ("no record for the instance", lambda h, p: None),
        (
            "record for another project",
            lambda h, p: _record(h, "core", project_path="/elsewhere", claude_session_id=SID),
        ),
        (
            "not a session id",
            lambda h, p: _record(h, "core", project_path=str(p), claude_session_id="--dangerously-skip"),
        ),
        ("no transcript with that id", lambda h, p: _record(h, "core", project_path=str(p), claude_session_id=SID)),
        (
            "transcript started elsewhere",
            lambda h, p: (
                _record(h, "core", project_path=str(p), claude_session_id=SID),
                _transcript(h, SID, "/somewhere/else"),
            ),
        ),
        (
            "unreadable record",
            lambda h, p: (h / ".empirica" / "instance_projects" / "core.json").write_text("{not json"),
        ),
    ],
)
def test_any_guard_that_fails_gives_no_id(home, why, setup):
    h, proj = home
    setup(h, proj)

    assert resume_ids.last_session_id("core", str(proj)) is None, why


def test_no_instance_id_gives_no_id(home):
    assert resume_ids.last_session_id(None, str(home[1])) is None


# ── the command ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _proj(launch: str, resume: str | None = None) -> ProjectSpec:
    return ProjectSpec(name="p", path="/p", launch=launch, resume=resume)


@pytest.mark.parametrize(
    "launch, expected",
    [
        ("claude", f"claude --resume {SID}"),
        ("claude --model opus", f"claude --model opus --resume {SID}"),
        ("claude --continue", f"claude --resume {SID}"),  # the launch's own continue is replaced by the exact session
        ("claude -c --model opus", f"claude --model opus --resume {SID}"),
        ("claude --resume", f"claude --resume {SID}"),  # a bare --resume would open the picker
        ("claude --resume aaaa-explicit", "claude --resume aaaa-explicit"),  # the launch names a session: kept
        ("codex", f"codex resume {SID}"),  # refresh passes codex only the id from ecodex's own pane record
    ],
)
def test_a_session_id_resumes_that_session(launch, expected):
    assert T.resume_command(_proj(launch), session_id=SID) == expected


def test_without_an_id_the_command_is_unchanged():
    assert T.resume_command(_proj("claude")) == "claude --continue"


def test_an_explicit_resume_line_still_wins():
    assert T.resume_command(_proj("claude", resume="claude --resume mine"), session_id=SID) == "claude --resume mine"


# ── refresh ──────────────────────────────────────────────────────────────────────────────────────────────────────────────


class FakeTmux:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def __call__(self, *args, check=False):
        self.calls.append(args)
        out = "\n".join("\t".join(r) for r in self.rows) if args and args[0] == "list-panes" else ""
        return subprocess.CompletedProcess(args, 0, out, "")


def _refresh(monkeypatch, proj: Path, iid: str):
    cfg = LauncherConfig(
        session_name="t",
        projects=[ProjectSpec(name="p", path=str(proj), launch="claude")],
        groups=[GroupSpec(name="g", panes=[PaneSpec(project_ref="p")])],
    )
    tmux = FakeTmux([("%1", "1", "claude", "g/0", iid, "g", str(proj))])
    monkeypatch.setattr(T, "_tmux", tmux)
    monkeypatch.setattr(T, "tmux_available", lambda: True)
    monkeypatch.setattr(T, "cockpit_session_exists", lambda _n: True)
    monkeypatch.setattr(T, "strip_session_identity", lambda _n: None)
    monkeypatch.setattr(T, "assign_identities", lambda _c, _s: {"g/0": iid})
    return T.refresh_cockpit(cfg)


def test_refresh_resumes_the_pane_s_own_session_and_says_so(home, monkeypatch):
    h, proj = home
    _record(h, "core", project_path=str(proj), claude_session_id=SID)
    _transcript(h, SID, str(proj))

    (entry,) = _refresh(monkeypatch, proj, "core").respawned

    assert entry["command"] == f"claude --resume {SID}"
    assert entry["session_id"] == SID


def test_refresh_falls_back_to_continue_when_the_session_cannot_be_confirmed(home, monkeypatch):
    h, proj = home
    _record(h, "core", project_path=str(proj), claude_session_id=SID)  # its own transcript is gone
    _transcript(h, "another-session", str(proj))  # but the directory has a conversation to continue

    (entry,) = _refresh(monkeypatch, proj, "core").respawned

    assert entry["command"] == "claude --continue"
    assert entry["session_id"] is None


def test_refresh_starts_fresh_when_the_directory_has_no_conversation_at_all(home, monkeypatch):
    """--continue there exits 1 ("No conversation found to continue") and the pane stays dead (prop_vc67kjtw4vfj3b5uavfqyjsycu)."""
    h, proj = home
    _record(h, "core", project_path=str(proj), claude_session_id=SID)

    (entry,) = _refresh(monkeypatch, proj, "core").respawned

    assert entry["command"] == "claude"


# ── 1.14.23 sweep (finding fddc9b63) ─────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "launch, expected",
    [
        ("claude --resume=aaaa-explicit", "claude --resume=aaaa-explicit"),  # names its session with =
        ('claude --append-system-prompt "say -c then -r" -c', 'claude --append-system-prompt "say -c then -r" -c'),
        ("claude --continue; exec bash", "claude --continue; exec bash"),
        ("claude --continue && echo done", "claude --continue && echo done"),
    ],
)
def test_a_launch_line_that_is_not_a_simple_command_is_not_edited(launch, expected):
    assert T.resume_command(_proj(launch), session_id=SID) == expected


def test_session_init_runs_after_clear_so_the_record_follows_the_new_conversation(tmp_path):
    from empirica.cli.command_handlers.setup_claude_code import _register_all_hooks

    settings: dict = {}
    _register_all_hooks(settings, tmp_path / "plugin", "python3", "json")
    matchers = [e["matcher"] for e in settings["hooks"]["SessionStart"] if "session-init.py" in json.dumps(e)]
    assert any("clear" in m.split("|") for m in matchers)


def test_refresh_clears_a_respawned_pane_s_waiting_mark(home, monkeypatch):
    h, proj = home
    flags = h / ".empirica" / "pane_waiting"
    flags.mkdir(parents=True)
    (flags / "1").write_text('{"label": "question"}')

    _refresh(monkeypatch, proj, "core")

    assert not (flags / "1").exists()


# ── codex panes: ecodex's pane_sessions record (prop_yv6d62yl5jgkbkayjmb47wel3e) ─────────────────────────────────────────


def _pane_record(h, pane="7", **over):
    import json

    data = {
        "harness": "codex",
        "session_id": "thread-abc12345",
        "project_path": "",
        "started": "x",
        "tmux_server_start": 1700,
    }
    data.update(over)
    d = h / ".empirica" / "pane_sessions"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pane}.json").write_text(json.dumps(data))


def test_a_codex_pane_record_is_trusted_when_harness_project_and_server_match(home):
    h, proj = home
    _pane_record(h, project_path=str(proj))
    assert resume_ids.codex_session_id("%7", str(proj), 1700) == "thread-abc12345"


@pytest.mark.parametrize(
    "why, over, server",
    [
        ("a restarted tmux server reuses pane numbers", {}, 1800),
        ("another harness wrote it", {"harness": "claude"}, 1700),
        ("another project", {"project_path": "/elsewhere"}, 1700),
        ("an id that is not a plain token", {"session_id": "x; rm -rf ~"}, 1700),
    ],
)
def test_a_codex_pane_record_that_fails_a_guard_is_ignored(home, why, over, server):
    h, proj = home
    _pane_record(h, **{"project_path": str(proj), **over})
    assert resume_ids.codex_session_id("%7", str(proj), server) is None, why


def test_no_record_or_no_server_start_gives_no_codex_id(home):
    assert resume_ids.codex_session_id("%7", str(home[1]), 1700) is None
    assert resume_ids.codex_session_id("%7", str(home[1]), None) is None

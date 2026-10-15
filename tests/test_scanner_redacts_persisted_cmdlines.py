"""The scanner must not write credentials into the snapshots it persists.

``empirica scan --save`` recorded every process command line verbatim. Ten listener ``curl`` processes carry an ntfy bearer token
in argv, so the saved snapshots (``last_scan_<project>.json`` and ``scans/<id>.json``, mode 664) held ten live tokens each time a
scan ran. Command lines are now redacted before they are clipped and stored, and the files are created owner-only.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from empirica.cli.command_handlers import scan_commands
from empirica.core.scanner import processes

TOKEN = "tk_abcdefghijklmnopqrstuvwxyz0123456789"  # noqa: S105 - a made-up token with the real shape


class _Proc:
    def __init__(self, cmdline: list[str]):
        self._cmdline = cmdline

    def as_dict(self, attrs):
        return {
            "pid": 4242,
            "ppid": 1,
            "cmdline": self._cmdline,
            "create_time": 1.0,
            "cwd": "/work",
            "num_fds": 3,
            "cpu_percent": 0.0,
            "memory_info": None,
            "name": "curl",
            "username": "u",
        }


def _row(cmdline: list[str]) -> dict:
    row = processes._row_for_proc(_Proc(cmdline), scanner_pid=1, now=10.0)
    assert row is not None
    return row


def test_a_bearer_token_in_argv_is_not_stored():
    row = _row(["curl", "-sSN", "-H", f"Authorization: Bearer {TOKEN}", "https://ntfy.example/topic/sse"])
    assert TOKEN not in row["cmdline"] and "tk_abcdefghij" not in row["cmdline"]
    assert "Bearer" in row["cmdline"] and "<redacted>" in row["cmdline"], "say which credential, not its value"
    assert "https://ntfy.example/topic/sse" in row["cmdline"]


def test_the_unquoted_header_split_across_argv_words_is_redacted_too():
    """The live listeners pass the header as two words: -H 'Authorization:' 'Bearer tk_...'."""
    row = _row(["curl", "-H", "Authorization:", f"Bearer {TOKEN}", "https://x"])
    assert TOKEN not in row["cmdline"]


def test_a_flag_style_key_is_redacted():
    assert "hunter2hunter2" not in _row(["tool", "--api-key=hunter2hunter2"])["cmdline"]


def test_a_command_line_without_a_secret_is_unchanged():
    row = _row(["python3", "-m", "http.server", "8000", "--bind", "127.0.0.1"])
    assert row["cmdline"] == "python3 -m http.server 8000 --bind 127.0.0.1"


def test_redaction_happens_before_clipping_so_a_cut_token_leaves_no_fragment():
    padding = "x" * 495
    cmd = ["curl", padding, "-H", f"Authorization: Bearer {TOKEN}", "https://x"]
    row = _row(cmd)
    assert len(row["cmdline"]) <= 512
    assert "tk_" not in row["cmdline"] or "tk_<redacted>" in row["cmdline"]
    assert "abcdefg" not in row["cmdline"], "a fragment of the token survived the clip"


def test_a_redactor_failure_does_not_become_a_leak(monkeypatch):
    monkeypatch.setattr(processes, "redact_secrets", lambda _t: "<redacted>")
    assert _row(["curl", "-H", f"Authorization: Bearer {TOKEN}"])["cmdline"] == "<redacted>"


# --- persisted files ------------------------------------------------------------------------------------------------


def _snap(scan_id: str, snapshot: dict | None = None) -> dict:
    return {
        "scan_id": scan_id,
        "started_at": "2026-10-15T00:00:00Z",
        "finished_at": "2026-10-15T00:00:01Z",
        "host": "testhost",
        "errors": [],
        "snapshot": snapshot if snapshot is not None else {},
    }


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(scan_commands, "_empirica_home", lambda: tmp_path)
    return tmp_path


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_every_persisted_file_is_owner_only(home):
    paths = scan_commands._persist_scan(_snap("s1", {"processes": []}), "proj")
    assert paths.get("last_scan_path"), "the project snapshot was not written"
    for key in ("scan_path", "last_scan_path"):
        assert _mode(Path(paths[key])) == 0o600, key
    history = home / "scan_history_proj.jsonl"
    assert history.exists() and _mode(history) == 0o600


def test_an_existing_group_readable_snapshot_is_tightened_when_it_is_rewritten(home):
    last = home / "last_scan_proj.json"
    last.write_text("{}")
    os.chmod(last, 0o664)
    scan_commands._persist_scan(_snap("s2"), "proj")
    assert _mode(last) == 0o600, "open('w') keeps the old mode; the rewrite must not"


def test_the_scans_directory_is_not_world_readable(home):
    scan_commands._persist_scan(_snap("s3"), "proj")
    assert _mode(home / "scans") & 0o077 == 0


def test_the_persisted_snapshot_still_round_trips(home):
    paths = scan_commands._persist_scan(_snap("s4", {"processes": [{"pid": 1}]}), "proj")
    assert json.loads(Path(paths["last_scan_path"]).read_text())["snapshot"]["processes"] == [{"pid": 1}]

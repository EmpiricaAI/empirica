"""`~/.claude.json` is Claude Code's LIVE store — setup must not replace it.

Reported by a remote user 2026-09-09, outside issues/PRs, and correct. Verified:
that file carries project history, costs and session state (115KB, 100 top-level
keys, 21 tracked projects on one box) and Claude Code writes it continuously.

Setup did read-modify-write of the whole file with an atomic rename. The rename is
what makes it LOSSY rather than merely racy — the replacement is total, so anything
another process wrote between our read and our rename is gone with no trace. And
running `empirica setup-claude-code` from inside a Claude Code session is the
obvious thing to do, which IS that window.

The reporter understated it. `_ensure_json_file` swallowed a JSONDecodeError and
returned the DEFAULT, so a read that caught the file mid-write parsed as garbage,
fell back to `{"mcpServers": {}}`, and the write replaced 115KB of live state with
a file containing only our own entry.
"""

from __future__ import annotations

import json

import pytest

from empirica.cli.command_handlers.setup_claude_code import (
    ConcurrentlyModified,
    _ensure_json_file,
    _read_json_with_stamp,
    _write_json_file,
)


def test_an_ABSENT_file_returns_the_default(tmp_path):
    """The legitimate half of the old behaviour, kept."""
    assert _ensure_json_file(tmp_path / "nope.json", {"mcpServers": {}}) == {"mcpServers": {}}


def test_a_CORRUPT_file_RAISES_rather_than_returning_the_default(tmp_path):
    """The catastrophic half, removed.

    Absent means "start from default". Exists-but-unparseable means "I do not
    know what is in here", and the only safe act on that is to refuse — because
    the caller's next move is to write the whole file back.
    """
    p = tmp_path / "c.json"
    p.write_text('{"projects": {"a": 1}, "cost')  # truncated mid-write
    with pytest.raises(json.JSONDecodeError):
        _ensure_json_file(p, {"mcpServers": {}})


def test_a_write_REFUSES_when_the_file_changed_under_us(tmp_path):
    """The read-modify-write window, detected.

    Not a lock — a detector. The caller decides whether to re-merge or tell the
    human, and either beats replacing state it never saw.
    """
    p = tmp_path / "live.json"
    p.write_text(json.dumps({"projects": {"a": 1}}))
    _, stamp = _read_json_with_stamp(p, {})

    # Another process writes while we were "preparing".
    p.write_text(json.dumps({"projects": {"a": 1, "b": 2}, "costs": 9}))

    with pytest.raises(ConcurrentlyModified):
        _write_json_file(p, {"projects": {"a": 1}}, expect_stamp=stamp)

    surviving = json.loads(p.read_text())
    assert surviving["projects"] == {"a": 1, "b": 2}, "the other process's write must survive"
    assert surviving["costs"] == 9, "and so must the keys we never read"


def test_an_UNCHANGED_file_still_writes(tmp_path):
    """Positive control. The refusal test alone would pass against a writer that
    refused unconditionally — which would break every setup run."""
    p = tmp_path / "live.json"
    p.write_text(json.dumps({"projects": {"a": 1}}))
    data, stamp = _read_json_with_stamp(p, {})
    data["mcpServers"] = {"empirica": {"command": "x"}}

    _write_json_file(p, data, expect_stamp=stamp)

    got = json.loads(p.read_text())
    assert got["mcpServers"]["empirica"]["command"] == "x"
    assert got["projects"] == {"a": 1}, "unrelated keys survive the merge"


def test_writing_a_NEW_file_needs_no_stamp(tmp_path):
    p = tmp_path / "new.json"
    _write_json_file(p, {"mcpServers": {}}, expect_stamp=None)
    assert json.loads(p.read_text()) == {"mcpServers": {}}


def test_the_temp_file_is_per_process_not_a_fixed_name(tmp_path):
    """`path.with_suffix('.tmp')` gave every concurrent setup the SAME temp path.

    Asserted by observing that no fixed-name temp survives and the write lands —
    the leak of a shared name would show up as two runs corrupting each other,
    which a single-process test cannot stage. What it CAN pin is that the temp is
    cleaned up and does not sit beside the target.
    """
    p = tmp_path / "x.json"
    _write_json_file(p, {"a": 1})
    leftovers = [q.name for q in tmp_path.iterdir() if ".tmp" in q.name]
    assert leftovers == [], f"temp files must not survive the write: {leftovers}"
    assert not (tmp_path / "x.tmp").exists(), "the old fixed-name temp must be gone"

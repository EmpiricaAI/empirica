"""projects-sync printed an error, discovered nothing, and exited 0.

The consumer that matters is not the human reading the error text — it is every
cron job, hook and CI step that trusts the exit status. Those recorded a silent
no-op as a successful sync, indefinitely, and nothing downstream could surface the
absence. Under `--output json` it was worse: the failure path printed human text,
so a caller got neither parseable output nor a failing status.

THE TRIGGER WAS THE INTERPRETER VERSION
---------------------------------------
`Path.is_file()` is not version-stable on a permission error: up to CPython 3.12 it
RAISES on EACCES, and from 3.13 it swallows the OSError and returns False. The walk
guarded `iterdir()` and left the `is_file()` probe bare, so one unreadable directory
aborted the whole walk on 3.12 and was invisible on 3.13+.

That is why the same command, same empirica version, aborted on one practitioner's
box and could not be reproduced on another's — and why two people each reasonably
concluded the other's environment was at fault. **A defect whose trigger is the
interpreter version presents as environmental flakiness**, which is the shape that
resists reproduction hardest, because both parties are observing correctly.
"""

from __future__ import annotations

import json
import os
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from empirica.cli.command_handlers.projects_commands import discover_projects, handle_projects_sync_command


@pytest.fixture
def tree_with_unreadable_dir(tmp_path: Path):
    """A valid project beside a directory nobody can read."""
    good = tmp_path / "good-project" / ".empirica"
    good.mkdir(parents=True)
    (good / "project.yaml").write_text("ai_id: good-project\n")

    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)

    # Root ignores the mode bits entirely, so the fixture would silently not be
    # testing anything. Prove the lock took effect rather than assuming it did.
    try:
        readable = (locked / ".empirica" / "project.yaml").is_file() is not None
    except OSError:
        readable = False
    if readable and os.access(locked, os.R_OK):
        os.chmod(locked, 0o700)
        pytest.skip("cannot create an unreadable directory here (running as root?)")

    yield tmp_path
    os.chmod(locked, 0o700)


def test_one_unreadable_directory_does_not_abort_the_walk(tree_with_unreadable_dir):
    """The regression, and it only ever failed on some interpreters — which is
    precisely why it has to be asserted rather than reasoned about."""
    manifest = discover_projects(roots=[tree_with_unreadable_dir], max_depth=4)

    names = {Path(p["path"]).name for p in manifest.get("projects", [])}
    assert "good-project" in names, "the readable project was lost to an unreadable sibling"


def test_the_walk_still_finds_nothing_in_an_empty_tree(tmp_path):
    """NEGATIVE CONTROL. A walk that returned a project unconditionally would satisfy
    the test above while discovering nothing real."""
    assert discover_projects(roots=[tmp_path], max_depth=4).get("projects") == []


# ── the exit contract ────────────────────────────────────────────────────────


def _failing_args(output: str) -> Namespace:
    return Namespace(
        roots=["/nonexistent-zz"],
        output=output,
        max_depth=3,
        include_hidden=False,
        no_cortex=True,
        no_write=True,
        dry_run=False,
        prune=False,
    )


def test_a_failed_sync_exits_non_zero(monkeypatch, capsys):
    """THE bug. The handler returned None on the error path and None maps to exit 0,
    so a crashed sync was indistinguishable from a clean one to anything automated."""
    monkeypatch.setattr(
        "empirica.cli.command_handlers.projects_commands.discover_projects",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    rc = handle_projects_sync_command(_failing_args("human"))
    assert rc == 1, "a sync that raised must not report success"


def test_the_json_contract_survives_the_failure_path(monkeypatch, capsys):
    """`--output json` promised JSON and emitted human error text when it mattered
    most. A caller parsing the output got an exception on top of a silent no-op."""
    monkeypatch.setattr(
        "empirica.cli.command_handlers.projects_commands.discover_projects",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    rc = handle_projects_sync_command(_failing_args("json"))
    assert rc == 1

    payload = json.loads(capsys.readouterr().out)  # raises if it is not JSON
    assert payload["ok"] is False
    assert "boom" in payload["error"], "the reason must survive into the structured form"


def test_a_clean_run_still_reports_success(monkeypatch, capsys):
    """POSITIVE CONTROL on the exit contract. Returning 1 unconditionally would pass
    both failure tests while breaking every successful sync."""
    monkeypatch.setattr(
        "empirica.cli.command_handlers.projects_commands.discover_projects",
        lambda *a, **k: {"projects": [{"path": "/tmp/x", "ai_id": "x"}]},
    )
    rc = handle_projects_sync_command(
        Namespace(
            roots=["."],
            output="json",
            max_depth=1,
            include_hidden=False,
            no_cortex=True,
            no_write=True,
            dry_run=True,
            prune=False,
        )
    )
    assert rc in (None, 0), f"a clean dry run must not report failure (got {rc!r})"


@pytest.mark.skipif(sys.version_info >= (3, 13), reason="documents the pre-3.13 behaviour this guard exists for")
def test_the_interpreter_difference_this_guard_exists_for(tmp_path):
    """Runs only where the raise actually happens, so on CI's older interpreters this
    asserts the real historical behaviour rather than a belief about it."""
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o000)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("mode bits not enforced here")
        with pytest.raises(OSError):
            (locked / ".empirica" / "project.yaml").is_file()
    finally:
        os.chmod(locked, 0o700)

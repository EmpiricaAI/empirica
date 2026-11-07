"""Defects the 1.14.22 pre-publish sweep found in the decision record, each pinned.

- A command whose first word is not a program name was written to the record verbatim (`API-KEY=sk-... ./run`), against
  the promise that only hashes of the input are kept.
- In a practice that does not ignore `.empirica/`, the hooks' own writes there landed between the two snapshots, so every
  sampled call read as an `empirica_state` change.
- Commands classed `unknown` (a script, `make`, `docker`) were marked reliable and so escaped the forced sample.
- `sudo -u bob rm -rf x` and `nice -n 10 rm -rf x` took the option's value for the program; `awk` running `system()`,
  `yq -i` and `fd -x` read as changing nothing.
- A git call that timed out gave an empty status, which compares as a change; a rename entry in `status -z` was garbled.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from empirica.core import decision_record as dr


def _cls(command: str) -> str:
    return dr.command_class("Bash", {"command": command})


@pytest.mark.parametrize(
    "command, secret",
    [
        ("API-KEY=sk-live-123 ./run", "sk-live-123"),
        ('"$(cat ~/.secrets/token)" x', "token"),
        ("'my password here' --flag", "password"),
    ],
)
def test_a_first_word_that_is_not_a_program_name_is_never_written(command, secret):
    name = _cls(command)
    assert secret not in name
    rec = dr.build(tool_name="Bash", tool_input={"command": command}, decision="allow", rule="pipeline")
    assert secret not in json.dumps(rec)


@pytest.mark.parametrize(
    "command, name",
    [
        ("git status", "git"),
        ("FOO=1 /usr/bin/git push", "git"),
        ("./deploy.sh", "deploy.sh"),
        ("python3 -m pytest", "python3"),
    ],
)
def test_a_program_name_is_still_recorded(command, name):
    assert _cls(command) == name


def _infer(command):
    return dr.infer_shell_effects(command, cwd="/w/p", project_path="/w/p", empirica_is_read=lambda _: False)


@pytest.mark.parametrize(
    "command", ["./deploy.sh", "make", "docker compose up -d", "xargs rm -rf", "find . -exec rm {} +"]
)
def test_a_command_the_text_cannot_class_is_unreliable(command):
    got = _infer(command)
    assert "unknown" in got["classes"] and got["reliable"] is False


@pytest.mark.parametrize("command", ["sudo -u bob rm -rf build", "nice -n 10 rm -rf build", "sudo -E rm -rf build"])
def test_a_wrapper_s_option_value_is_not_the_program(command):
    assert "delete_recursive" in _infer(command)["classes"]
    assert _cls(command) == "rm"


@pytest.mark.parametrize(
    "command",
    [
        "awk 'BEGIN { system(\"rm -rf x\") }'",
        "awk '{ print > \"out.txt\" }' in.txt",
        "yq -i '.a = 1' f.yaml",
        "fd -x rm",
        "fd . -X rm",
    ],
)
def test_the_writing_modes_of_reading_programs_are_not_no_effect(command):
    got = _infer(command)
    assert got["classes"] != [] and got["reliable"] is False


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def tracked_practice(tmp_path, monkeypatch):
    """A practice that tracks .empirica/ (18 of 30 under the owner's tree do)."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("project_id: p\n")
    (proj / "a.txt").write_text("a\n")
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(proj, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return proj


def test_the_hooks_own_writes_under_dot_empirica_are_not_an_observed_effect(tracked_practice):
    before = dr.snapshot(str(tracked_practice))
    dr.append({"n": 1}, tracked_practice / ".empirica" / "decisions.jsonl")  # what the gate itself does
    (tracked_practice / ".empirica" / "hook_counters_x.json").write_text("{}")

    assert dr.observed_effects(before, dr.snapshot(str(tracked_practice)), str(tracked_practice)) == []


def test_a_rename_is_one_entry_with_its_real_paths(tracked_practice):
    _git(tracked_practice, "mv", "a.txt", "renamed.txt")
    status = dr.snapshot(str(tracked_practice))["status"]

    assert set(status) == {"renamed.txt", "a.txt"}


def test_a_git_that_fails_gives_no_snapshot_rather_than_an_empty_one(tracked_practice, monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired("git", 2)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(dr.SnapshotFailed):
        dr.snapshot(str(tracked_practice))
    monkeypatch.undo()
    assert dr.store_before("toolu_t", str(tracked_practice), decision="allow", sampled=True) is True


def test_storing_a_before_snapshot_prunes_old_orphans(tracked_practice):
    import os
    import time

    old = dr.snapshot_path("toolu_orphan")
    old.parent.mkdir(parents=True, exist_ok=True)
    old.write_text("{}")
    past = time.time() - 2 * 24 * 3600
    os.utime(old, (past, past))

    dr.store_before("toolu_new", str(tracked_practice), decision="allow", sampled=True)

    assert not old.exists() and dr.snapshot_path("toolu_new").exists()


# ── second pass (1.14.22 sweep) ─────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("command", ["API-KEY=sk/abcSECRETtail123 git push", "X-TOKEN=Zm9v/YmFyc2VjcmV0 curl x"])
def test_a_secret_with_a_slash_in_a_non_identifier_assignment_is_not_recorded(command):
    name = _cls(command)
    assert name == "unparsed"
    assert "SECRET" not in name and "YmFy" not in name


def test_a_rename_reported_in_the_worktree_column_is_one_entry(tracked_practice):
    (tracked_practice / "a.txt").rename(tracked_practice / "new.txt")
    subprocess.run(["git", "add", "-N", "new.txt"], cwd=tracked_practice, check=True, capture_output=True)

    status = dr.snapshot(str(tracked_practice))["status"]

    assert set(status) <= {"new.txt", "a.txt"} and "new.txt" in status

"""Part A of ecodex's ruling (prop_4avo25wpczfcpmv7ug5fbztztq): observe what a sampled shell call changed.

A before-and-after snapshot, paired on tool_use_id: the practice's git status (with untracked files, and the size and mtime of
each listed path, so a second change to a dirty file shows), its refs, and a fixed list of configuration files. It cannot see
network, process, remote or outside writes, and the post line says so in observation_scope, so an empty observed set reads as
"nothing in this view changed", not "nothing changed". Sampled: every call whose text cannot settle its effect, plus a
deterministic 1 in 10 of the rest.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from empirica.core import decision_record as dr

REPO = Path(__file__).resolve().parents[1]
HOOKS = REPO / "empirica" / "plugins" / "claude-code-integration" / "hooks"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".empirica").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    return h


@pytest.fixture
def practice(tmp_path, home):
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("project_id: p\n")
    (proj / ".gitignore").write_text(".empirica/*\n!.empirica/project.yaml\n")
    (proj / "a.txt").write_text("a\n")
    _git(proj, "init", "-q", "-b", "main")
    _git(proj, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(proj, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init")
    return proj


def _observe(proj: Path, change) -> list[str]:
    before = dr.snapshot(str(proj))
    change()
    return dr.observed_effects(before, dr.snapshot(str(proj)), str(proj))


# ── sampling ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_every_call_the_text_cannot_settle_is_sampled():
    assert all(dr.sampled(f"toolu_{i}", reliable=False) for i in range(200))


def test_about_one_in_ten_of_the_rest_is_sampled_and_the_same_id_always_gets_the_same_answer():
    ids = [f"toolu_{i:05d}" for i in range(2000)]
    share = sum(dr.sampled(i, reliable=True) for i in ids) / len(ids)

    assert 0.07 < share < 0.13
    assert [dr.sampled(i, reliable=True) for i in ids[:50]] == [dr.sampled(i, reliable=True) for i in ids[:50]]


def test_a_call_with_no_id_is_not_sampled():
    assert dr.sampled(None, reliable=False) is False


# ── what the snapshot sees ────────────────────────────────────────────────────────────────────────────────────────────


def test_nothing_changed_is_an_empty_set(practice):
    assert _observe(practice, lambda: None) == []


def test_a_new_file_in_the_practice_is_a_project_write(practice):
    assert _observe(practice, lambda: (practice / "b.txt").write_text("b\n")) == ["project_write"]


def test_a_second_change_to_an_already_dirty_file_is_seen(practice):
    (practice / "a.txt").write_text("dirty\n")

    assert _observe(practice, lambda: (practice / "a.txt").write_text("dirtier, and longer\n")) == ["project_write"]


def test_a_commit_is_vcs_local(practice):
    def commit():
        (practice / "c.txt").write_text("c\n")
        _git(practice, "add", "c.txt")
        _git(practice, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c")

    assert "vcs_local" in _observe(practice, commit)


def test_a_push_is_vcs_remote(practice, tmp_path):
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    _git(practice, "remote", "add", "origin", str(remote))

    assert "vcs_remote" in _observe(practice, lambda: _git(practice, "push", "-q", "origin", "main"))


def test_a_shell_rc_change_is_config_write(practice, home):
    assert _observe(practice, lambda: (home / ".bashrc").write_text("export X=1\n")) == ["config_write"]


def test_the_snapshot_does_not_rewrite_the_index(practice):
    index = practice / ".git" / "index"
    before = index.stat().st_mtime_ns
    (practice / "a.txt").touch()  # stat-dirty: plain git status would refresh the index
    dr.snapshot(str(practice))

    assert index.stat().st_mtime_ns == before


# ── the pair on tool_use_id ───────────────────────────────────────────────────────────────────────────────────────────


def test_a_before_is_stored_only_for_an_allowed_sampled_call_in_a_practice(practice):
    assert dr.store_before("toolu_a", str(practice), decision="allow", sampled=True) is True
    assert dr.store_before("toolu_b", str(practice), decision="deny", sampled=True) is False
    assert dr.store_before("toolu_c", str(practice), decision="allow", sampled=False) is False
    assert dr.store_before("toolu_d", None, decision="allow", sampled=True) is False
    assert dr.snapshot_path("toolu_a").exists()
    assert not dr.snapshot_path("toolu_b").exists()


def _hook(hook: str, payload: dict, home: Path, cwd: Path):
    subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO)},
        timeout=60,
    )


def test_the_post_line_carries_what_was_observed_and_what_could_be_seen(practice, home):
    payload = {
        "session_id": "s",
        "tool_use_id": "toolu_e2e",
        "tool_name": "Bash",
        "tool_input": {"command": "python3 make_files.py"},
        "cwd": str(practice),
    }
    _hook("sentinel-gate.py", {"hook_event_name": "PreToolUse", **payload}, home, practice)
    pre = [json.loads(line) for line in (practice / ".empirica" / "decisions.jsonl").read_text().splitlines()]
    assert pre[-1]["sampled"] is True and pre[-1]["inferred_reliable"] is False
    if pre[-1]["decision"] != "allow":
        pytest.skip(f"the gate did not allow the call here ({pre[-1]['rule']}); the pair is covered by the unit tests")
    assert dr.snapshot_path("toolu_e2e").exists()

    (practice / "made.txt").write_text("x\n")  # what the command did
    _hook(
        "truncation-legibility.py",
        {"hook_event_name": "PostToolUse", **payload, "tool_response": {"stdout": ""}},
        home,
        practice,
    )

    post = json.loads((practice / ".empirica" / "decisions.jsonl").read_text().splitlines()[-1])
    assert post["event"] == "post" and post["sampled"] is True
    assert post["effect_classes"] == ["project_write"] and post["observed"] is True
    assert post["observation_scope"] == list(dr.OBSERVATION_SCOPE)
    assert not dr.snapshot_path("toolu_e2e").exists()


def test_an_unsampled_shell_call_still_says_its_effect_was_not_observed(practice, home):
    rec = dr.build_outcome(
        {"tool_name": "Bash", "tool_use_id": "toolu_none", "tool_input": {"command": "ls"}}, "success", str(practice)
    )

    assert rec["effect_classes"] is None and rec["observed"] is False and rec["sampled"] is False


# ── staging and committing move the index, not the files (unknown a1ac63b8, goal 3fcde812) ────────────────────────────────
# Any change to a path's status code read as project_write, so `git add` and `git commit` chains observed a project write
# that no inference predicted: two of the five reliable misses in the 2026-11-13 precision measurement.


def _commit(proj: Path, *paths: str) -> None:
    _git(proj, "add", *paths)
    _git(proj, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c")


def test_staging_a_modified_file_is_an_index_change(practice):
    (practice / "a.txt").write_text("changed\n")
    assert _observe(practice, lambda: _git(practice, "add", "a.txt")) == ["vcs_local"]


def test_committing_a_modified_file_is_not_a_project_write(practice):
    (practice / "a.txt").write_text("changed\n")
    assert _observe(practice, lambda: _commit(practice, "a.txt")) == ["vcs_local"]


def test_discarding_a_change_still_writes_the_project(practice):
    (practice / "a.txt").write_text("changed\n")
    assert "project_write" in _observe(practice, lambda: _git(practice, "checkout", "--", "a.txt"))


def test_editing_an_already_modified_file_still_writes_the_project(practice):
    (practice / "a.txt").write_text("changed\n")
    assert _observe(practice, lambda: (practice / "a.txt").write_text("changed again, longer\n")) == ["project_write"]

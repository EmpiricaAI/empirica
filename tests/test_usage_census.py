"""The usage census behind /capability-review (goal cfc94d52): what Empirica gets used for, read from Claude Code's session
logs and the practice's store, so the keep / promote / retire decision rests on counts, not memory.

Built under tmp_path: tests must not measure the box (no real ~/.claude, no real store).
"""

from __future__ import annotations

import json
import sqlite3
import time

from empirica.core import usage_census as uc


def _transcript(path, *calls):
    lines = []
    for kind, value in calls:
        block = (
            {"type": "tool_use", "name": "Bash", "input": {"command": value}}
            if kind == "bash"
            else {"type": "tool_use", "name": "Skill", "input": {"skill": value}}
        )
        lines.append(json.dumps({"message": {"content": [block]}}))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_only_real_commands_are_counted_and_aliases_fold_into_their_command(tmp_path):
    _transcript(
        tmp_path / "p1" / "a.jsonl",
        ("bash", "empirica goals-list --output json"),
        ("bash", "cd x && empirica gl"),  # gl is an alias of goals-list
        ("bash", "echo 'empirica core is great'"),  # prose, not a command
        ("bash", "empirica finding-log --finding x; empirica finding-log --finding y"),
        ("skill", "empirica:forecast"),
    )
    usage = uc.transcript_usage(tmp_path, since=0)
    assert usage["transcripts"] == 1
    assert usage["commands"]["goals-list"] == 2 and usage["commands"]["finding-log"] == 2
    assert "core" not in usage["commands"] and usage["aliases_used"]["gl"] == 1
    assert usage["skills"]["forecast"] == 1


def test_a_transcript_older_than_the_window_is_skipped(tmp_path):
    old = tmp_path / "p" / "old.jsonl"
    _transcript(old, ("bash", "empirica goals-list"))
    t = time.time() - 90 * 86400
    import os

    os.utime(old, (t, t))
    assert uc.transcript_usage(tmp_path, since=time.time() - 30 * 86400)["transcripts"] == 0


def test_the_census_names_what_was_never_used_and_what_points_at_it(tmp_path):
    _transcript(tmp_path / "p" / "a.jsonl", ("bash", "empirica goals-list"), ("skill", "forecast"))
    out = uc.census(days=30, projects_root=tmp_path, conn=None)
    assert "goals-list" not in out["commands"]["unused"] and "efficiency-report" in out["commands"]["unused"]
    assert "gl" not in out["commands"]["unused"]  # an alias is reported with aliases, not as a command
    assert "forecast" not in out["skills"]["never_loaded"] and "pre-publish" in out["skills"]["never_loaded"]
    assert isinstance(out["commands"]["references"].get("efficiency-report"), list)
    assert out["limits"]  # what the census cannot see is part of the result


def test_artifact_counts_come_from_the_store_inside_the_window(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE project_findings (id TEXT, created_timestamp REAL)")
    conn.execute("CREATE TABLE decisions (id TEXT, created_timestamp REAL)")
    now = time.time()
    conn.executemany("INSERT INTO project_findings VALUES (?, ?)", [("a", now), ("b", now - 90 * 86400)])
    counts = uc.artifact_counts(conn, since=now - 30 * 86400)
    assert counts["finding"] == 1 and counts["decision"] == 0 and "unknown" not in counts  # a missing table is skipped


def test_a_skill_a_person_typed_as_a_slash_command_counts_as_loaded(tmp_path):
    """1.17.14 docs sweep: only the Skill tool was counted, so skills people load by typing /name read as never loaded."""
    path = tmp_path / "p" / "a.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            json.dumps(x)
            for x in (
                {
                    "type": "user",
                    "message": {"role": "user", "content": "<command-name>/empirica:forecast</command-name>"},
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "text", "text": "<command-name>/pre-publish</command-name>"}],
                    },
                },
                {"type": "user", "message": {"role": "user", "content": "<command-name>/compact</command-name>"}},
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "<command-name>/render</command-name>"}]},
                },
            )
        )
        + "\n"
    )
    skills = uc.transcript_usage(tmp_path, since=0)["skills"]
    assert skills["forecast"] == 1 and skills["pre-publish"] == 1
    assert "render" not in skills  # an assistant quoting the tag is not a person loading the skill


def test_unused_aliases_carry_their_references_too(tmp_path):
    out = uc.census(days=30, projects_root=tmp_path, conn=None)
    assert set(out["aliases"]["references"]) == set(out["aliases"]["unused"])


def test_calls_with_an_env_prefix_timeout_or_a_path_are_counted(tmp_path):
    _transcript(
        tmp_path / "p" / "a.jsonl",
        ("bash", "FOO=1 empirica goals-list"),
        ("bash", "timeout 30 empirica goals-list"),
        ("bash", "/home/u/.local/bin/empirica goals-list"),
    )
    assert uc.transcript_usage(tmp_path, since=0)["commands"]["goals-list"] == 3


def test_another_plugins_skill_with_the_same_name_is_not_ours(tmp_path):
    _transcript(tmp_path / "p" / "a.jsonl", ("skill", "anthropic-skills:forecast"), ("skill", "empirica:forecast"))
    assert uc.transcript_usage(tmp_path, since=0)["skills"]["forecast"] == 1


def test_a_reference_to_a_longer_command_does_not_count_for_its_prefix(tmp_path):
    (tmp_path / "hook.py").write_text("run('empirica check-submit -')\n")
    assert uc.static_references(["check", "check-submit"], package_root=tmp_path) == {
        "check": [],
        "check-submit": ["hook.py"],
    }


def test_a_repository_path_ending_in_empirica_is_not_a_call(tmp_path):
    _transcript(tmp_path / "p" / "a.jsonl", ("bash", "git -C /home/u/empirical-ai/empirica status"))
    assert "status" not in uc.transcript_usage(tmp_path, since=0)["commands"]

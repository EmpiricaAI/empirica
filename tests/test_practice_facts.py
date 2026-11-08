"""The generated, static part of a practice's instructions (.empirica/practice-facts.md).

A practice's own CLAUDE.md is written by people and grows slowly; this file says what the practice's data already says
(identity, mesh address, type and what it means, domain and how work is judged there), so a practice has instructions
from its first session. It sits in the cached front of the context, so its bytes must not change unless its inputs do
(David, 2026-11-08): no timestamps, sorted output, and no rewrite when nothing changed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("yaml")

from empirica.core import practice_facts as pf


def _practice(tmp_path, body: str):
    proj = tmp_path / "p"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text(body)
    return proj


FULL = (
    "name: outreach\nai_id: empirica-outreach\ndescription: Writes and publishes Empirica's posts\n"
    "type: content\ndomain: marketing\ncanonical_seat: empirica.david.empirica-outreach\n"
)


def test_the_facts_say_what_the_project_file_says(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    text = pf.render(_practice(tmp_path, FULL))

    assert "empirica.david.empirica-outreach" in text
    assert "Writes and publishes Empirica's posts" in text
    assert "content" in text and "marketing" in text
    from empirica.core.project_types import TYPE_MEANINGS

    assert TYPE_MEANINGS["content"] in text  # the type's meaning, from the one list


def test_the_domain_s_checks_and_hints_are_included(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    text = pf.render(_practice(tmp_path, FULL.replace("domain: marketing", "domain: research")))

    assert "Every claim needs a finding with evidence" in text  # research's hints_to_ai
    assert "artifact_breadth" in text  # a required check


def test_a_practice_with_no_type_or_domain_says_so_and_how_to_set_them(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    text = pf.render(_practice(tmp_path, "name: x\nai_id: x\n"))

    assert "project-update --type" in text and "project-update --domain" in text


def test_the_bytes_are_the_same_every_time(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proj = _practice(tmp_path, FULL)

    assert pf.render(proj) == pf.render(proj)


def test_it_is_written_only_when_it_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    proj = _practice(tmp_path, FULL)
    target = proj / ".empirica" / "practice-facts.md"

    assert pf.write(proj) is True
    first = target.stat().st_mtime_ns
    assert pf.write(proj) is False
    assert target.stat().st_mtime_ns == first

    (proj / ".empirica" / "project.yaml").write_text(FULL.replace("posts", "posts and talks"))
    assert pf.write(proj) is True
    assert "posts and talks" in target.read_text()


def test_no_project_file_writes_nothing(tmp_path):
    assert pf.write(tmp_path) is False
    assert not (tmp_path / ".empirica" / "practice-facts.md").exists()


def test_project_update_writes_the_facts(tmp_path, monkeypatch, capsys):
    import subprocess
    from types import SimpleNamespace

    import empirica.cli.command_handlers.project_update as pu
    import empirica.config.path_resolver as pr
    import empirica.data.repositories.workspace_db as wdb

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "co"
    (root / ".empirica").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr(pr, "get_git_root", lambda: root)
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: tmp_path / "absent" / "workspace.db")
    (root / ".empirica" / "project.yaml").write_text("project_id: p-x\nname: co\ntype: cli\n")

    pu.handle_project_update_command(SimpleNamespace(add_tag=["rust"], output="json"))

    facts = (root / ".empirica" / "practice-facts.md").read_text()
    assert "# This practice: co" in facts and "`cli`" in facts


# ── the CLAUDE.md seed ─────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "project_type, expect",
    [
        ("cli", "How it is built and tested"),
        ("content", "Voice and audience"),
        ("research", "Sources and how claims are cited"),
        ("operations", "Machines and services it touches"),
        ("legal", "What good work looks like here"),
        ("", "What good work looks like here"),
    ],
)
def test_a_new_practice_gets_a_seed_shaped_by_its_kind(tmp_path, project_type, expect):
    proj = _practice(tmp_path, f"name: p\nai_id: p\ndescription: Does p things\ntype: {project_type}\n")

    written = pf.seed_claude_md(proj)

    text = (proj / "CLAUDE.md").read_text()
    assert written == proj / "CLAUDE.md"
    assert "@.empirica/practice-facts.md" in text
    assert expect in text and "Does p things" in text
    assert "propose" in text and "approves" in text  # how it grows: by diffs the person approves


def test_an_existing_claude_md_is_never_touched(tmp_path):
    proj = _practice(tmp_path, "name: p\nai_id: p\ntype: cli\n")
    (proj / "CLAUDE.md").write_text("# mine\n")
    (proj / ".claude").mkdir()

    assert pf.seed_claude_md(proj) is None
    assert (proj / "CLAUDE.md").read_text() == "# mine\n"


def test_a_claude_md_under_dot_claude_counts_as_existing(tmp_path):
    proj = _practice(tmp_path, "name: p\nai_id: p\ntype: cli\n")
    (proj / ".claude").mkdir()
    (proj / ".claude" / "CLAUDE.md").write_text("# mine\n")

    assert pf.seed_claude_md(proj) is None
    assert not (proj / "CLAUDE.md").exists()


def test_a_repo_with_agents_md_keeps_it_loaded(tmp_path):
    proj = _practice(tmp_path, "name: p\nai_id: p\ntype: cli\n")
    (proj / "AGENTS.md").write_text("# agents\n")

    pf.seed_claude_md(proj)

    assert "@AGENTS.md" in (proj / "CLAUDE.md").read_text()


def test_the_seed_holds_no_personal_protocol_data(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".empirica").mkdir(parents=True)
    (home / ".empirica" / "workflow-protocol.yaml").write_text(
        "work_preferences:\n  someone:\n    ai_autonomy_level: full\n"
    )
    monkeypatch.setenv("HOME", str(home))
    proj = _practice(tmp_path, "name: p\nai_id: p\ntype: cli\n")

    pf.seed_claude_md(proj)

    assert "someone" not in (proj / "CLAUDE.md").read_text()

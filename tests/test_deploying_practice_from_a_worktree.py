"""A deploy run from a linked git worktree of a practice is attributed to that practice (David, 2026-10-26).

`.plugin-writer.json` records which practice last wrote the shared plugin directory. `_deploying_practice` took the cwd's
`.empirica/project.yaml` and nothing else, but a linked worktree has no `.empirica/` of its own (it is gitignored and lives in the main
checkout), so a deploy from one was stamped `ai_id: null, source: unknown`: exactly where an older or divergent checkout, the likeliest
to revert someone's newer files, lives. cortex measured 13 of 41 project paths on the fleet box as worktrees resolving to a parent practice.

mesh-support's ruling stands: identity comes only from sources that carry it (explicit, env, a practice checkout), never from where the
package sits. A linked worktree of an initialized practice IS that practice's checkout; the lookup goes through git's common directory and
refuses anything else. The source is labelled `cwd-worktree`, so doctor shows it was derived.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.cli.command_handlers import setup_claude_code as scc


def _git(path, *args):
    return subprocess.run(["git", *args], cwd=path, capture_output=True, text=True, check=True).stdout.strip()


def _practice(tmp_path, ai_id="empirica-demo", initialized=True):
    """A git repo that is a practice: .empirica/project.yaml (+ config.yaml when initialized), gitignored like the real thing."""
    main = tmp_path / "main"
    main.mkdir()
    _git(main, "init", "-q")
    _git(main, "config", "user.email", "t@t")
    _git(main, "config", "user.name", "t")
    (main / ".gitignore").write_text(".empirica/\n")
    (main / "f").write_text("x")
    _git(main, "add", ".")
    _git(main, "commit", "-qm", "i")
    (main / ".empirica").mkdir()
    (main / ".empirica" / "project.yaml").write_text(f"ai_id: {ai_id}\n")
    if initialized:
        (main / ".empirica" / "config.yaml").write_text("project: demo\n")
    return main


def _worktree(main, tmp_path, name="wt"):
    wt = tmp_path / name
    _git(main, "worktree", "add", "-q", "--detach", str(wt))
    return wt


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("EMPIRICA_AI_ID", raising=False)


def test_a_deploy_from_a_linked_worktree_is_attributed_to_the_practice_it_belongs_to(tmp_path, monkeypatch):
    main = _practice(tmp_path)
    wt = _worktree(main, tmp_path)
    assert not (wt / ".empirica").exists(), "control: the worktree really has no .empirica of its own"
    monkeypatch.chdir(wt)
    assert scc._deploying_practice() == ("empirica-demo", "cwd-worktree")


def test_a_subdirectory_of_the_worktree_resolves_the_same_way(tmp_path, monkeypatch):
    main = _practice(tmp_path)
    wt = _worktree(main, tmp_path)
    (wt / "deep" / "er").mkdir(parents=True)
    monkeypatch.chdir(wt / "deep" / "er")
    assert scc._deploying_practice() == ("empirica-demo", "cwd-worktree")


def test_the_main_checkout_is_still_plain_cwd(tmp_path, monkeypatch):
    """Control: unchanged behaviour where it already worked."""
    main = _practice(tmp_path)
    monkeypatch.chdir(main)
    assert scc._deploying_practice() == ("empirica-demo", "cwd")


def test_a_worktree_with_its_own_project_yaml_is_plain_cwd(tmp_path, monkeypatch):
    """A worktree whose .empirica was linked or copied in is read directly; the worktree path is only the fallback."""
    main = _practice(tmp_path)
    wt = _worktree(main, tmp_path)
    (wt / ".empirica").mkdir()
    (wt / ".empirica" / "project.yaml").write_text("ai_id: its-own\n")
    monkeypatch.chdir(wt)
    assert scc._deploying_practice() == ("its-own", "cwd")


def test_a_worktree_of_a_repo_that_is_not_an_initialized_practice_stays_unknown(tmp_path, monkeypatch):
    main = _practice(tmp_path, initialized=False)
    wt = _worktree(main, tmp_path)
    monkeypatch.chdir(wt)
    assert scc._deploying_practice() == (None, "unknown")


def test_a_directory_outside_any_repository_stays_unknown(tmp_path, monkeypatch):
    nowhere = tmp_path / "nowhere"
    nowhere.mkdir()
    monkeypatch.chdir(nowhere)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    assert scc._deploying_practice() == (None, "unknown")


def test_an_explicit_name_and_the_environment_still_win_over_the_worktree(tmp_path, monkeypatch):
    main = _practice(tmp_path)
    wt = _worktree(main, tmp_path)
    monkeypatch.chdir(wt)
    assert scc._deploying_practice("named-outright") == ("named-outright", "explicit")
    monkeypatch.setenv("EMPIRICA_AI_ID", "from-env")
    assert scc._deploying_practice() == ("from-env", "env")


def test_the_stamp_written_from_a_worktree_names_the_practice_and_says_how(tmp_path, monkeypatch):
    import json

    main = _practice(tmp_path)
    wt = _worktree(main, tmp_path)
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.chdir(wt)
    scc._write_plugin_version_stamp(plugin, "9.9.9")
    record = json.loads((plugin / ".plugin-writer.json").read_text())
    assert record["ai_id"] == "empirica-demo" and record["ai_id_source"] == "cwd-worktree"
    assert record["cwd"] == str(wt), "the record still says where the command actually ran"

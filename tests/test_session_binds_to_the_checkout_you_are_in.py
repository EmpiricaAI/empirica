"""A session created in a checkout belongs to THAT checkout's practice.

Measured live: a session created for `empirica`, from empirica's own checkout,
bound to a throwaway project that a scratch test had left in the resolver
context files minutes earlier. Context files won because the harness resets cwd
constantly — true when cwd is not a project, and exactly wrong when it is.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers import session_create as sc


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(
        "ai_id: theirs\nproject_id: 11111111-2222-3333-4444-555555555555\n"
    )
    monkeypatch.chdir(root)
    return root


def test_the_checkouts_own_project_wins_over_a_context_file(checkout, monkeypatch):
    monkeypatch.setattr(sc, "_resolve_from_context_files", lambda: "99999999-dead-dead-dead-999999999999")
    assert sc._resolve_early_project_id(None) == "11111111-2222-3333-4444-555555555555"


def test_context_files_still_answer_when_cwd_is_not_a_project(tmp_path, monkeypatch):
    """The case the old priority was written for, and it must keep working."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sc, "_resolve_from_context_files", lambda: "99999999-dead-dead-dead-999999999999")
    assert sc._resolve_early_project_id(None) == "99999999-dead-dead-dead-999999999999"


def test_an_explicit_project_id_still_wins_over_both(checkout, monkeypatch):
    monkeypatch.setattr(sc, "_resolve_from_context_files", lambda: "99999999-dead-dead-dead-999999999999")
    explicit = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"
    assert sc._resolve_early_project_id(explicit) == explicit


def test_a_project_yaml_without_an_id_falls_through(tmp_path, monkeypatch):
    root = tmp_path / "half"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text("ai_id: theirs\n")
    monkeypatch.chdir(root)
    monkeypatch.setattr(sc, "_resolve_from_context_files", lambda: "99999999-dead-dead-dead-999999999999")
    assert sc._resolve_early_project_id(None) == "99999999-dead-dead-dead-999999999999"

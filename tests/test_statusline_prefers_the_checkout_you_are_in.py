"""The statusline follows the checkout you are standing in, not a stale mapping.

It opened `<resolved project>/.empirica/sessions/sessions.db`, and resolved that
path from `instance_projects` with no guard — while the library resolver has one
and logs that it corrected the path. So a scratch `session-create` anywhere on
the box repointed the mapping, the statusline opened that other store, found no
session for this practice in it, and rendered `[empirica:inactive]`. It happened
to a working practitioner twice in one afternoon, and `inactive` gives a reader
no way to tell a stale mapping from a broken install.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SL = (
    Path(__file__).parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "scripts"
    / "statusline_empirica.py"
)
_spec = importlib.util.spec_from_file_location("statusline_for_guard_test", SL)
assert _spec is not None and _spec.loader is not None
sl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sl)


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "mine"
    (root / ".empirica" / "sessions").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text("ai_id: mine\nproject_id: 1111\n")
    (root / ".empirica" / "sessions" / "sessions.db").write_text("")
    monkeypatch.chdir(root)
    return root


def test_a_mapping_pointing_elsewhere_loses_to_the_checkout(checkout, tmp_path):
    elsewhere = str(tmp_path / "scratch")
    assert sl._prefer_cwd(elsewhere, str(checkout)) == str(checkout)


def test_a_mapping_that_agrees_is_kept(checkout):
    assert sl._prefer_cwd(str(checkout), str(checkout)) == str(checkout)


def test_without_a_checkout_the_mapping_stands(tmp_path, monkeypatch):
    """The case the mapping exists for: cwd is not a project, so it cannot speak."""
    monkeypatch.chdir(tmp_path)
    assert sl._cwd_project_root() is None
    assert sl._prefer_cwd("/somewhere/else", None) == "/somewhere/else"


def test_a_project_yaml_without_a_store_is_not_a_root(tmp_path, monkeypatch):
    half = tmp_path / "half"
    (half / ".empirica").mkdir(parents=True)
    (half / ".empirica" / "project.yaml").write_text("ai_id: half\n")
    monkeypatch.chdir(half)
    assert sl._cwd_project_root() is None, "no store here, so this cannot answer for a session"

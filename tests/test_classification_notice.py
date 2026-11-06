"""A practice whose project file needs a label is told so when it starts, and nothing is changed for it.

David, 2026-11-06: a practice that is not running gets its project type when he launches it, not from a central edit. So the practice
itself has to be told. At session start it sees one line when its project file holds no type, a legacy type, a type the code does not
know, or free text where a domain name belongs, with the command that sets it. A project that is labelled, or a directory that is not
a project, sees nothing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from empirica.core import project_types as pt

HOOKS = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(autouse=True)
def empty_home(tmp_path, monkeypatch):
    """The domain names include the user's own files under ~/.empirica/domains; keep the box out of it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))


def _project(tmp_path: Path, body: str | None) -> Path:
    root = tmp_path / "proj"
    (root / ".empirica").mkdir(parents=True)
    if body is not None:
        (root / ".empirica" / "project.yaml").write_text("project_id: p\nname: proj\n" + body)
    return root


# ── reading the file ────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("type: cli\ndomain: research\n", ("cli", "research")),
        ("type: 'web-app'\ndomain: \"ai/measurement\"  # old\n", ("web-app", "ai/measurement")),
        ("tags: [a]\n", (None, "")),
        ("type:\ndomain:\n", (None, "")),
        ("calibration_weights:\n  type: nested\n  domain: nested\n", (None, "")),  # only top-level keys count
    ],
)
def test_the_type_and_domain_are_read_from_top_level_lines(tmp_path, body, expected):
    assert pt.project_file_labels(_project(tmp_path, body)) == expected


def test_no_file_reads_as_nothing(tmp_path):
    assert pt.project_file_labels(_project(tmp_path, None)) == (None, "")
    assert pt.project_file_labels(tmp_path / "nowhere") == (None, "")


# ── the note ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "body", ["type: cli\n", "type: research\ndomain: research\n", "type: engagement\ndomain: consulting\n"]
)
def test_a_labelled_project_gets_no_note(tmp_path, body):
    assert pt.classification_note(_project(tmp_path, body)) == ""


def test_a_directory_that_is_not_a_project_gets_no_note(tmp_path):
    assert pt.classification_note(_project(tmp_path, None)) == ""
    assert pt.classification_note(tmp_path / "nowhere") == ""


def test_a_project_without_a_type_is_told_how_to_set_one(tmp_path):
    note = pt.classification_note(_project(tmp_path, "tags: [a]\n"))
    assert "no project type" in note
    assert "empirica project-update --type" in note and "--help" in note


@pytest.mark.parametrize("legacy", sorted(pt.LEGACY_TYPES))
def test_a_legacy_type_is_named_as_legacy(tmp_path, legacy):
    note = pt.classification_note(_project(tmp_path, f"type: {legacy}\n"))
    assert f"`{legacy}`" in note and "legacy" in note
    assert "empirica project-update --type" in note


def test_a_type_the_code_does_not_know_is_named(tmp_path):
    note = pt.classification_note(_project(tmp_path, "type: gizmo\n"))
    assert "`gizmo`" in note and "not a project type" in note


def test_free_text_where_a_domain_belongs_is_named_with_what_happens_to_it(tmp_path):
    note = pt.classification_note(_project(tmp_path, "type: cli\ndomain: ai/measurement\n"))
    assert "`ai/measurement`" in note and "tags" in note
    assert "empirica project-update" in note
    assert "project type" not in note  # the type is fine and is not mentioned


def test_both_problems_are_one_line(tmp_path):
    note = pt.classification_note(_project(tmp_path, "type: product\ndomain: ai/measurement\n"))
    assert "`product`" in note and "`ai/measurement`" in note
    assert "\n" not in note


# ── the session-start hook shows it ─────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def session_init(monkeypatch):
    sys.path.insert(0, str(HOOKS.parent / "lib"))
    spec = importlib.util.spec_from_file_location("session_init_classification", HOOKS / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    # The two other notices read the installed core and a deploy cache; neither is under test.
    monkeypatch.setattr(mod, "_core_version_notice", lambda: "")
    monkeypatch.setattr(mod, "_deploy_gap_block", lambda _root: "")
    return mod


def test_session_start_shows_the_note(session_init, tmp_path):
    notices = session_init._startup_notices(_project(tmp_path, "type: product\n"))
    assert "`product`" in notices and "empirica project-update --type" in notices


def test_session_start_says_nothing_for_a_labelled_project(session_init, tmp_path):
    assert session_init._startup_notices(_project(tmp_path, "type: cli\n")) == ""


def test_a_failure_to_build_the_note_does_not_break_session_start(session_init, tmp_path, monkeypatch):
    def boom(_path):
        raise RuntimeError("nope")

    monkeypatch.setattr(pt, "classification_note", boom)
    assert session_init._startup_notices(_project(tmp_path, "type: product\n")) == ""

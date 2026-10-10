"""A compaction restores its own session's snapshot, not the newest one in the project.

pre-compact detected the Empirica session as the latest ACTIVE session for the ai_id (recency across instances) and wrote
a snapshot whose top-level session_id was null; post-compact loaded the newest pre_summary_*.json in the project's
ref-docs whichever instance wrote it. With two live instances in one project, one cross-read the other's vectors and
open transaction. The claude session id is the identity both hooks have; the snapshot now carries it and post-compact
selects by it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


def _load(name, filename):
    sys.path.insert(0, str(HOOKS.parent / "lib"))
    spec = importlib.util.spec_from_file_location(name, HOOKS / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def post():
    return _load("post_compact_identity", "post-compact.py")


@pytest.fixture(scope="module")
def pre():
    return _load("pre_compact_identity", "pre-compact.py")


def _snapshot(directory, stamp, **fields):
    path = directory / f"pre_summary_{stamp}.json"
    path.write_text(json.dumps({"type": "pre_summary_snapshot", "timestamp": stamp, **fields}))
    return path


@pytest.fixture
def ref_docs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    directory = tmp_path / ".empirica" / "ref-docs"
    directory.mkdir(parents=True)
    return directory


def test_the_snapshot_of_this_session_wins_over_a_newer_one_from_a_sibling(post, ref_docs):
    _snapshot(ref_docs, "2026-10-10T10-00-00", claude_session_id="claude-A", empirica_session_id="sess-A", marker="A")
    _snapshot(ref_docs, "2026-10-10T10-05-00", claude_session_id="claude-B", empirica_session_id="sess-B", marker="B")
    assert post._load_pre_snapshot("claude-A", "sess-A")["marker"] == "A"
    assert post._load_pre_snapshot("claude-B", "sess-B")["marker"] == "B"


def test_a_snapshot_is_matched_by_its_transaction_identity_too(post, ref_docs):
    """Snapshots written before the identity stamp carry the owner only inside active_transaction."""
    _snapshot(ref_docs, "2026-10-10T10-00-00", active_transaction={"claude_session_id": "claude-A"}, marker="A")
    _snapshot(ref_docs, "2026-10-10T10-05-00", active_transaction={"claude_session_id": "claude-B"}, marker="B")
    assert post._load_pre_snapshot("claude-A", "sess-A")["marker"] == "A"


def test_a_siblings_snapshot_is_not_taken_when_nothing_of_ours_exists(post, ref_docs):
    _snapshot(ref_docs, "2026-10-10T10-05-00", claude_session_id="claude-B", empirica_session_id="sess-B")
    assert post._load_pre_snapshot("claude-A", "sess-A") is None


def test_a_snapshot_with_no_identity_still_loads_as_the_legacy_fallback(post, ref_docs):
    _snapshot(ref_docs, "2026-10-10T09-00-00", marker="legacy")
    _snapshot(ref_docs, "2026-10-10T10-05-00", claude_session_id="claude-B", empirica_session_id="sess-B")
    assert post._load_pre_snapshot("claude-A", "sess-A")["marker"] == "legacy"


def test_with_no_identity_to_match_the_newest_snapshot_is_loaded_as_before(post, ref_docs):
    _snapshot(ref_docs, "2026-10-10T09-00-00", marker="old")
    _snapshot(ref_docs, "2026-10-10T10-05-00", claude_session_id="claude-B", marker="new")
    assert post._load_pre_snapshot()["marker"] == "new"


def test_the_snapshot_identity_names_both_sessions(pre):
    assert pre._snapshot_identity("claude-A", "sess-A") == {
        "claude_session_id": "claude-A",
        "empirica_session_id": "sess-A",
    }


def test_pre_compact_resolves_its_own_session_before_falling_back_to_the_latest(pre, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "active_work_claude-A.json").write_text(json.dumps({"empirica_session_id": "sess-A"}))

    def not_this(*_a, **_k):
        raise AssertionError("fell back to the latest session although this one's identity is on disk")

    monkeypatch.setattr("empirica.utils.session_resolver.InstanceResolver.latest_session_id", staticmethod(not_this))
    assert pre._detect_empirica_session("claude-A") == "sess-A"


def test_pre_compact_still_falls_back_to_the_latest_session_without_an_identity(pre, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.latest_session_id", staticmethod(lambda **_k: "sess-latest")
    )
    monkeypatch.setattr("empirica.utils.session_resolver.InstanceResolver.ai_id", staticmethod(lambda: "x"))
    assert pre._detect_empirica_session() == "sess-latest"

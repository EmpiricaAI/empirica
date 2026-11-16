"""Shared practice repos: each person's seat in an untracked project.local.yaml (goal cdc2e27c, David 2026-11-16).

Two people work one Forgejo repo (empirica-consult: David and Francisco). Cortex joins both to the same project, so
project_id and ai_id are shared and tracked; only the per-person keys differ. A repo is shared only when its tracked
project.yaml says so (`seats: per-clone`, set by `empirica setup-claude-code --shared-repo`): then those keys live in each
clone's untracked `.empirica/project.local.yaml` and nowhere else. An unmarked practice behaves exactly as before.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

from empirica.utils import yaml_cache

_DAVID = {
    "tenant_slug": "david",
    "mesh_id_prefix": "empirica-consulting.david",
    "canonical_seat": "empirica-consulting.david.empirica-consult",
}
_FRANCISCO = {
    "tenant_slug": "francisco",
    "mesh_id_prefix": "empirica-consulting.francisco",
    "canonical_seat": "empirica-consulting.francisco.empirica-consult",
}


def _practice(tmp_path, *, shared, tracked_person=None, local=None):
    emp = tmp_path / ".empirica"
    emp.mkdir()
    tracked = {"project_id": "p-1", "ai_id": "empirica-consult", "org_id": "org-c", **(tracked_person or {})}
    if shared:
        tracked["seats"] = "per-clone"
    (emp / "project.yaml").write_text(yaml.safe_dump(tracked))
    if local is not None:
        (emp / "project.local.yaml").write_text(yaml.safe_dump(local))
    yaml_cache.clear_cache()
    return tmp_path


def _read(root, name):
    path = root / ".empirica" / name
    return yaml.safe_load(path.read_text()) if path.exists() else None


# --- readers ---


def test_in_a_shared_repo_the_clones_own_seat_is_read(tmp_path):
    data = yaml_cache.load_project_yaml(_practice(tmp_path, shared=True, local=_FRANCISCO))
    assert data["canonical_seat"] == _FRANCISCO["canonical_seat"] and data["tenant_slug"] == "francisco"
    assert data["project_id"] == "p-1" and data["ai_id"] == "empirica-consult"


def test_in_a_shared_repo_a_seat_pulled_from_git_is_not_this_clones(tmp_path):
    """A tracked seat (someone committed theirs) must not be taken as this person's."""
    data = yaml_cache.load_project_yaml(_practice(tmp_path, shared=True, tracked_person=_DAVID))
    assert "canonical_seat" not in data and "tenant_slug" not in data


def test_only_the_per_person_keys_are_taken_from_the_local_file(tmp_path):
    local = {**_FRANCISCO, "project_id": "evil", "ai_id": "x", "tenant_slug": 123}
    data = yaml_cache.load_project_yaml(_practice(tmp_path, shared=True, local=local))
    assert data["project_id"] == "p-1" and data["ai_id"] == "empirica-consult"
    assert "tenant_slug" not in data  # not a string: ignored
    assert data["mesh_id_prefix"] == "empirica-consulting.francisco"


def test_an_unmarked_practice_reads_exactly_as_before(tmp_path):
    data = yaml_cache.load_project_yaml(_practice(tmp_path, shared=False, tracked_person=_DAVID, local=_FRANCISCO))
    assert data["tenant_slug"] == "david" and data["canonical_seat"] == _DAVID["canonical_seat"]


def test_the_cached_tracked_file_is_not_mutated(tmp_path):
    root = _practice(tmp_path, shared=True, tracked_person=_DAVID, local=_FRANCISCO)
    yaml_cache.load_project_yaml(root)
    assert yaml_cache.load_yaml_cached(root / ".empirica" / "project.yaml")["tenant_slug"] == "david"


# --- the writer: setup and the session-start heal ---


def _persist(root, **kw):
    from empirica.cli.command_handlers.setup_claude_code import _persist_tenant_metadata

    return _persist_tenant_metadata(root, **{"org_id": "org-c", "tenant_slug": None, "mesh_id_prefix": None, **kw})


def test_in_a_shared_repo_a_persons_seat_is_written_locally_and_never_tracked(tmp_path):
    root = _practice(tmp_path, shared=True)
    assert _persist(root, tenant_slug="francisco", mesh_id_prefix="empirica-consulting.francisco") is True
    assert _read(root, "project.local.yaml") == _FRANCISCO
    tracked = _read(root, "project.yaml")
    assert not any(k in tracked for k in _FRANCISCO) and tracked["seats"] == "per-clone"


def test_in_a_shared_repo_a_prefix_alone_still_goes_local(tmp_path):
    root = _practice(tmp_path, shared=True, tracked_person={"tenant_slug": "david"})
    _persist(root, mesh_id_prefix="empirica-consulting.francisco")
    assert _read(root, "project.local.yaml")["canonical_seat"] == _FRANCISCO["canonical_seat"]
    assert "mesh_id_prefix" not in _read(root, "project.yaml")


def test_in_a_shared_repo_the_seat_is_never_composed_from_the_tracked_prefix(tmp_path):
    root = _practice(tmp_path, shared=True, tracked_person={"mesh_id_prefix": "empirica-consulting.david"})
    _persist(root, tenant_slug="francisco")
    assert "canonical_seat" not in (_read(root, "project.local.yaml") or {})
    tracked = _read(root, "project.yaml")
    assert tracked.get("tenant_slug") is None and "canonical_seat" not in tracked  # nothing of this person's is tracked


def test_an_unmarked_practice_whose_tenant_changes_is_corrected_in_place(tmp_path):
    """A single owner's renamed tenant fixes the tracked file, as before the overlay."""
    root = _practice(tmp_path, shared=False, tracked_person={"tenant_slug": "old", "mesh_id_prefix": "o.old"})
    _persist(root, org_id="o2", tenant_slug="new", mesh_id_prefix="o2.new")
    tracked = _read(root, "project.yaml")
    assert (tracked["tenant_slug"], tracked["org_id"], tracked["canonical_seat"]) == (
        "new",
        "o2",
        "o2.new.empirica-consult",
    )
    assert _read(root, "project.local.yaml") is None


def test_marking_a_repo_shared_takes_every_persons_keys_out_of_git(tmp_path):
    from empirica.cli.command_handlers.setup_claude_code import mark_shared_repo

    root = _practice(tmp_path, shared=False, tracked_person={**_DAVID, "tenant": "david"}, local={"keep": "me"})
    assert mark_shared_repo(root) is True
    tracked = _read(root, "project.yaml")
    assert tracked["seats"] == "per-clone" and not any(k in tracked for k in (*_DAVID, "tenant"))
    assert _read(root, "project.local.yaml") == {
        "keep": "me"
    }  # this clone's file is not overwritten with someone's keys
    assert ".empirica/project.local.yaml" in (root / ".gitignore").read_text()
    assert mark_shared_repo(root) is False  # already shared


def test_a_tracked_legacy_tenant_is_not_this_clones_either(tmp_path):
    data = yaml_cache.load_project_yaml(_practice(tmp_path, shared=True, tracked_person={"tenant": "david"}))
    assert "tenant" not in data


def test_the_local_file_is_kept_out_of_git(tmp_path):
    root = _practice(tmp_path, shared=True)
    _persist(root, tenant_slug="francisco", mesh_id_prefix="empirica-consulting.francisco")
    assert ".empirica/project.local.yaml" in (root / ".gitignore").read_text()


# --- the session-start heal ---


def _hook():
    path = (
        Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "session-init.py"
    )
    spec = importlib.util.spec_from_file_location("session_init_overlay", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["session_init_overlay"] = mod
    spec.loader.exec_module(mod)
    return mod


def _heal(monkeypatch, root):
    calls = []
    monkeypatch.setattr("empirica.core.auth.cortex_bearer", lambda *a, **k: calls.append("me") or {"url": None})
    _hook()._heal_mesh_metadata_at_init(str(root))
    return calls


def test_the_heal_counts_this_clones_local_seat(tmp_path, monkeypatch):
    assert _heal(monkeypatch, _practice(tmp_path, shared=True, local=_FRANCISCO)) == []


def test_the_heal_asks_cortex_when_only_a_pulled_seat_exists(tmp_path, monkeypatch):
    """A fresh clone of a shared repo heals its own seat instead of acting as whoever committed theirs."""
    assert _heal(monkeypatch, _practice(tmp_path, shared=True, tracked_person=_DAVID)) == ["me"]

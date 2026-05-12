"""Tests for empirica/api/registry.py (v1.10.0 daemon multi-project)."""

from __future__ import annotations

from pathlib import Path

import yaml

from empirica.api.registry import (
    REGISTRY_VERSION,
    find_by_project_id,
    list_known_projects,
    load_registry,
    prune_stale,
    save_registry,
    upsert_project,
)


# ─── load / save round-trip ────────────────────────────────────────────


def test_load_missing_returns_empty(tmp_path: Path):
    p = tmp_path / "registry.yaml"
    reg = load_registry(p)
    assert reg == {"version": REGISTRY_VERSION, "projects": []}


def test_load_unparseable_returns_empty(tmp_path: Path):
    p = tmp_path / "registry.yaml"
    p.write_text(": invalid yaml :{:[\n", encoding="utf-8")
    reg = load_registry(p)
    assert reg["projects"] == []


def test_load_non_dict_returns_empty(tmp_path: Path):
    p = tmp_path / "registry.yaml"
    p.write_text("- just a list\n- not a dict\n", encoding="utf-8")
    reg = load_registry(p)
    assert reg["projects"] == []


def test_save_then_load_round_trip(tmp_path: Path):
    p = tmp_path / "registry.yaml"
    reg = {
        "version": 1,
        "projects": [
            {
                "project_id": "abc-uuid",
                "slug": "alpha",
                "name": "Alpha",
                "path": "/tmp/alpha",
                "repo_url": "https://example.com/alpha",
                "last_seen": "2026-05-12T08:00:00+00:00",
            },
        ],
    }
    save_registry(reg, p)
    assert p.exists()
    loaded = load_registry(p)
    assert loaded["projects"] == reg["projects"]


def test_save_creates_parent_dir(tmp_path: Path):
    p = tmp_path / "nested" / "dir" / "registry.yaml"
    save_registry({"version": 1, "projects": []}, p)
    assert p.exists()


def test_save_is_atomic(tmp_path: Path):
    """Verify tempfile + rename — no .registry-*.tmp left behind."""
    p = tmp_path / "registry.yaml"
    save_registry({"version": 1, "projects": [{"project_id": "x"}]}, p)
    tmp_files = list(tmp_path.glob(".registry-*.tmp"))
    assert tmp_files == [], f"Tempfiles leaked: {tmp_files}"


# ─── upsert ────────────────────────────────────────────────────────────


def test_upsert_inserts_new_entry():
    reg = {"version": 1, "projects": []}
    upsert_project(
        reg, project_id="new-id", slug="new", name="New", path="/tmp/new"
    )
    assert len(reg["projects"]) == 1
    assert reg["projects"][0]["project_id"] == "new-id"
    assert "last_seen" in reg["projects"][0]


def test_upsert_updates_existing_entry():
    reg = {
        "version": 1,
        "projects": [
            {"project_id": "x", "slug": "old", "name": "Old", "path": "/tmp/old"},
        ],
    }
    upsert_project(
        reg, project_id="x", slug="new", name="New", path="/tmp/new",
        repo_url="https://example.com/x",
    )
    assert len(reg["projects"]) == 1
    assert reg["projects"][0]["slug"] == "new"
    assert reg["projects"][0]["name"] == "New"
    assert reg["projects"][0]["repo_url"] == "https://example.com/x"


def test_upsert_preserves_explicit_last_seen():
    reg = {"version": 1, "projects": []}
    upsert_project(
        reg, project_id="x", slug="s", name="n", path="/p",
        last_seen="2026-01-01T00:00:00+00:00",
    )
    assert reg["projects"][0]["last_seen"] == "2026-01-01T00:00:00+00:00"


# ─── find_by_project_id ────────────────────────────────────────────────


def test_find_by_project_id_hit():
    reg = {
        "version": 1,
        "projects": [
            {"project_id": "a", "name": "A"},
            {"project_id": "b", "name": "B"},
        ],
    }
    entry = find_by_project_id(reg, "b")
    assert entry is not None
    assert entry["name"] == "B"


def test_find_by_project_id_miss():
    reg = {"version": 1, "projects": [{"project_id": "a"}]}
    assert find_by_project_id(reg, "nope") is None


# ─── prune_stale ───────────────────────────────────────────────────────


def test_prune_keeps_existing_with_empirica_dir(tmp_path: Path):
    proj = tmp_path / "alive"
    (proj / ".empirica").mkdir(parents=True)
    reg = {
        "version": 1,
        "projects": [{"project_id": "alive", "path": str(proj)}],
    }
    kept_reg, removed = prune_stale(reg)
    assert len(kept_reg["projects"]) == 1
    assert removed == []


def test_prune_removes_missing_path(tmp_path: Path):
    reg = {
        "version": 1,
        "projects": [{"project_id": "gone", "path": str(tmp_path / "nonexistent")}],
    }
    kept_reg, removed = prune_stale(reg)
    assert kept_reg["projects"] == []
    assert len(removed) == 1
    assert removed[0]["project_id"] == "gone"


def test_prune_removes_path_without_empirica_dir(tmp_path: Path):
    proj = tmp_path / "barren"
    proj.mkdir()
    reg = {
        "version": 1,
        "projects": [{"project_id": "barren", "path": str(proj)}],
    }
    kept_reg, removed = prune_stale(reg)
    assert kept_reg["projects"] == []
    assert len(removed) == 1


def test_prune_removes_empty_path():
    reg = {"version": 1, "projects": [{"project_id": "x", "path": ""}]}
    kept_reg, removed = prune_stale(reg)
    assert kept_reg["projects"] == []
    assert len(removed) == 1


# ─── list_known_projects ───────────────────────────────────────────────


def test_list_known_projects_round_trip(tmp_path: Path):
    p = tmp_path / "registry.yaml"
    reg = {
        "version": 1,
        "projects": [
            {"project_id": "a", "name": "A", "path": "/p/a"},
            {"project_id": "b", "name": "B", "path": "/p/b"},
        ],
    }
    save_registry(reg, p)
    projects = list_known_projects(p)
    assert len(projects) == 2
    assert {p["project_id"] for p in projects} == {"a", "b"}


def test_list_known_projects_empty_when_missing(tmp_path: Path):
    p = tmp_path / "registry.yaml"
    assert list_known_projects(p) == []


# ─── YAML shape verification ───────────────────────────────────────────


def test_saved_yaml_is_human_readable(tmp_path: Path):
    """Registry should be hand-editable — comments allowed, no flow style."""
    p = tmp_path / "registry.yaml"
    reg = {
        "version": 1,
        "projects": [{"project_id": "x", "name": "X", "path": "/p"}],
    }
    save_registry(reg, p)
    text = p.read_text(encoding="utf-8")
    # Block style (multiple lines per dict, not {key: value, ...})
    assert "{" not in text
    assert "project_id: x" in text
    # Round-trip through yaml.safe_load to verify it parses
    parsed = yaml.safe_load(text)
    assert parsed["version"] == 1

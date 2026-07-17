"""Tests for empirica/api/registry.py (v1.9.6 daemon multi-project)."""

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
    warn_name_collisions,
)
from empirica.api.registry import (
    dedupe_registry as _dedupe_registry,
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
    upsert_project(reg, project_id="new-id", slug="new", name="New", path="/tmp/new")
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
        reg,
        project_id="x",
        slug="new",
        name="New",
        path="/tmp/new",
        repo_url="https://example.com/x",
    )
    assert len(reg["projects"]) == 1
    assert reg["projects"][0]["slug"] == "new"
    assert reg["projects"][0]["name"] == "New"
    assert reg["projects"][0]["repo_url"] == "https://example.com/x"


def test_upsert_preserves_explicit_last_seen():
    reg = {"version": 1, "projects": []}
    upsert_project(
        reg,
        project_id="x",
        slug="s",
        name="n",
        path="/p",
        last_seen="2026-01-01T00:00:00+00:00",
    )
    assert reg["projects"][0]["last_seen"] == "2026-01-01T00:00:00+00:00"


_UUID_A = "2273f11d-0000-4000-8000-000000000000"


def test_upsert_by_path_rekey_updates_not_appends():
    """The registry-drift fix: re-syncing a project at the SAME path with a NEW
    project_id (slug→UUID promotion) must UPDATE the existing entry, not append a
    fresh-UUID duplicate (which produced the dedup-conflict spam)."""
    reg = {
        "version": 1,
        "projects": [{"project_id": "grants", "slug": "grants", "name": "Grants", "path": "/tmp/grants"}],
    }
    upsert_project(reg, project_id=_UUID_A, slug="grants", name="Grants", path="/tmp/grants")
    assert len(reg["projects"]) == 1  # updated in place — NOT two entries
    assert reg["projects"][0]["project_id"] == _UUID_A  # canonical UUID took over


def test_upsert_by_path_does_not_downgrade_canonical_uuid_to_slug():
    """Canonical-identity guard: a same-path re-sync must not clobber a UUID
    entry with a slug (UUID-keyed wins)."""
    reg = {
        "version": 1,
        "projects": [{"project_id": _UUID_A, "slug": "grants", "name": "Grants", "path": "/tmp/grants"}],
    }
    upsert_project(reg, project_id="grants", slug="grants", name="Grants Renamed", path="/tmp/grants")
    assert len(reg["projects"]) == 1
    assert reg["projects"][0]["project_id"] == _UUID_A  # UUID preserved
    assert reg["projects"][0]["name"] == "Grants Renamed"  # other fields still updated


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


# ─── dual-key dedup (auto-self-heal on load) ────────────────────────────


def _entry(*, project_id: str, slug: str, path: str, last_seen: str = "") -> dict:
    return {
        "project_id": project_id,
        "slug": slug,
        "name": slug,
        "path": path,
        "repo_url": None,
        "last_seen": last_seen,
    }


def test_dedupe_keeps_uuid_drops_slug_on_same_path():
    """Two entries, same path: UUID-keyed wins, slug-keyed dropped."""
    uuid_id = "d0317d3b-1234-4567-89ab-cdef01234567"
    registry = {
        "version": 1,
        "projects": [
            _entry(project_id="empirica-cortex", slug="empirica-cortex", path="/home/me/empirica-cortex"),
            _entry(project_id=uuid_id, slug="empirica-cortex", path="/home/me/empirica-cortex"),
        ],
    }
    deduped, removed = _dedupe_registry(registry)
    assert len(deduped["projects"]) == 1
    assert deduped["projects"][0]["project_id"] == uuid_id
    assert len(removed) == 1
    assert removed[0]["project_id"] == "empirica-cortex"


def test_dedupe_leaves_distinct_paths_alone():
    """Different paths are not the dedup target — stale data is prune-stale's
    job, not dedup's."""
    registry = {
        "version": 1,
        "projects": [
            _entry(project_id="empirica", slug="empirica", path="/home/me/empirica_back_2_4_26"),
            _entry(project_id="748a81a2-1234-4567-89ab-cdef01234567", slug="empirica", path="/home/me/empirica"),
        ],
    }
    deduped, removed = _dedupe_registry(registry)
    assert len(deduped["projects"]) == 2
    assert removed == []


def test_dedupe_skips_uuid_conflict():
    """Two UUID-keyed entries at the same path = genuine conflict; keep both
    + warn rather than silently drop one."""
    registry = {
        "version": 1,
        "projects": [
            _entry(project_id="aaaaaaaa-1111-4222-8333-444444444444", slug="proj", path="/home/me/proj"),
            _entry(project_id="bbbbbbbb-1111-4222-8333-444444444444", slug="proj", path="/home/me/proj"),
        ],
    }
    deduped, removed = _dedupe_registry(registry)
    assert len(deduped["projects"]) == 2  # both kept
    assert removed == []


def test_dedupe_all_legacy_keeps_most_recent():
    """No UUID entries in the group — keep the one with the latest
    `last_seen`."""
    registry = {
        "version": 1,
        "projects": [
            _entry(project_id="old-slug", slug="proj", path="/home/me/proj", last_seen="2025-01-01T00:00:00"),
            _entry(project_id="newer-slug", slug="proj", path="/home/me/proj", last_seen="2026-06-07T00:00:00"),
        ],
    }
    deduped, removed = _dedupe_registry(registry)
    assert len(deduped["projects"]) == 1
    assert deduped["projects"][0]["project_id"] == "newer-slug"
    assert len(removed) == 1


def test_dedupe_passes_through_pathless_entries():
    """Entries missing a path are malformed but not dedup's target — keep
    them untouched."""
    uuid_id = "12345678-1234-4567-89ab-cdef01234567"
    registry = {
        "version": 1,
        "projects": [
            {
                "project_id": uuid_id,
                "slug": "ok",
                "name": "ok",
                "path": "/home/me/ok",
                "repo_url": None,
                "last_seen": "",
            },
            {
                "project_id": "no-path",
                "slug": "broken",
                "name": "broken",
                "path": "",
                "repo_url": None,
                "last_seen": "",
            },
        ],
    }
    deduped, removed = _dedupe_registry(registry)
    assert len(deduped["projects"]) == 2
    assert removed == []


def test_dedupe_six_paths_doubled_realistic_load(tmp_path: Path):
    """End-to-end: write a registry resembling David's actual broken
    state (6 paths doubled), load_registry auto-dedupes + persists, the
    file on disk shrinks correspondingly."""
    six = [
        "ecodex",
        "empirica-autonomy",
        "empirica-cortex",
        "empirica-extension",
        "empirica-outreach",
        "empirica-workspace",
    ]
    projects = []
    for i, name in enumerate(six):
        uuid_id = f"{i:08}-1111-4222-8333-444444444444"
        path = f"/home/me/{name}"
        projects.append(_entry(project_id=name, slug=name, path=path))
        projects.append(_entry(project_id=uuid_id, slug=name, path=path))
    p = tmp_path / "registry.yaml"
    save_registry({"version": 1, "projects": projects}, p)

    reg = load_registry(p)  # auto_dedupe=True default
    assert len(reg["projects"]) == 6
    for entry in reg["projects"]:
        # All survivors are UUID-keyed
        assert "-" in entry["project_id"]
        assert len(entry["project_id"]) == 36

    # File on disk persists the dedup
    reread = yaml.safe_load(p.read_text())
    assert len(reread["projects"]) == 6


def test_load_with_auto_dedupe_false_returns_raw(tmp_path: Path):
    """Inspection paths can disable auto-dedupe to see the raw state."""
    uuid_id = "abcdef01-1234-4567-89ab-cdef01234567"
    registry = {
        "version": 1,
        "projects": [
            _entry(project_id="slug-id", slug="proj", path="/p"),
            _entry(project_id=uuid_id, slug="proj", path="/p"),
        ],
    }
    p = tmp_path / "registry.yaml"
    save_registry(registry, p)
    reg = load_registry(p, auto_dedupe=False)
    assert len(reg["projects"]) == 2  # raw, no dedup


# ─── name-collision warn guard (backup/clone shadow detection) ──────────


def test_warn_name_collisions_flags_same_name_diff_path():
    """Same display name at two different paths = a backup/clone shadow.

    Regression guard for the 2026-07-17 registry-ghost: an
    empirica_back_2_4_26 backup entry and the live empirica repo both
    registered as "Empirica", shadowing the live project on lookup.
    """
    registry = {
        "version": 1,
        "projects": [
            _entry(project_id="empirica", slug="Empirica", path="/home/x/empirica_back"),
            _entry(project_id="748a81a2-0000-4000-8000-000000000000", slug="Empirica", path="/home/x/empirica"),
        ],
    }
    collisions = warn_name_collisions(registry)
    assert len(collisions) == 1
    assert collisions[0]["name"] == "Empirica"
    assert collisions[0]["paths"] == ["/home/x/empirica", "/home/x/empirica_back"]


def test_warn_name_collisions_ignores_same_path():
    """Same name AND same path is a dedupe target, not a shadow — no warning."""
    registry = {
        "version": 1,
        "projects": [
            _entry(project_id="slug-id", slug="Empirica", path="/home/x/empirica"),
            _entry(project_id="uuid-id", slug="Empirica", path="/home/x/empirica"),
        ],
    }
    assert warn_name_collisions(registry) == []


def test_warn_name_collisions_ignores_distinct_names():
    """Distinct names at distinct paths are just distinct projects."""
    registry = {
        "version": 1,
        "projects": [
            _entry(project_id="a", slug="Alpha", path="/home/x/alpha"),
            _entry(project_id="b", slug="Beta", path="/home/x/beta"),
        ],
    }
    assert warn_name_collisions(registry) == []


def test_load_registry_warns_but_keeps_both_shadow_entries(tmp_path: Path, caplog):
    """load_registry surfaces the collision (warn) without removing either
    entry — dedupe only collapses same-path, shadows are surfaced not fixed."""
    import logging

    registry = {
        "version": 1,
        "projects": [
            _entry(project_id="empirica", slug="Empirica", path="/home/x/empirica_back"),
            _entry(project_id="748a81a2-0000-4000-8000-000000000000", slug="Empirica", path="/home/x/empirica"),
        ],
    }
    p = tmp_path / "registry.yaml"
    save_registry(registry, p)
    with caplog.at_level(logging.WARNING):
        reg = load_registry(p)
    assert len(reg["projects"]) == 2  # both kept — surfaced, not removed
    assert any("share name 'Empirica'" in r.message for r in caplog.records)

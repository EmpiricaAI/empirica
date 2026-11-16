"""Shared practice repos: each person's seat in an untracked project.local.yaml (goal cdc2e27c, David 2026-11-16).

Two people work one Forgejo repo (empirica-consult: David and Francisco). Cortex joins both to the same project, so
project_id and ai_id are shared and tracked; only the per-person keys differ, and they cannot live in the tracked file
for both clones. `.empirica/project.local.yaml` (untracked) overrides exactly those keys.
"""

from __future__ import annotations

import yaml

from empirica.utils import yaml_cache


def _practice(tmp_path, local=None):
    emp = tmp_path / ".empirica"
    emp.mkdir()
    (emp / "project.yaml").write_text(
        yaml.safe_dump(
            {
                "project_id": "p-1",
                "ai_id": "empirica-consult",
                "org_id": "org-c",
                "tenant_slug": "david",
                "mesh_id_prefix": "empirica-consulting.david",
                "canonical_seat": "empirica-consulting.david.empirica-consult",
            }
        )
    )
    if local is not None:
        (emp / "project.local.yaml").write_text(yaml.safe_dump(local))
    yaml_cache.clear_cache()
    return tmp_path


_FRANCISCO = {"tenant_slug": "francisco", "mesh_id_prefix": "empirica-consulting.francisco",
              "canonical_seat": "empirica-consulting.francisco.empirica-consult"}  # fmt: skip


def test_the_local_file_gives_this_clone_its_own_seat(tmp_path):
    data = yaml_cache.load_project_yaml(_practice(tmp_path, _FRANCISCO))
    assert data["tenant_slug"] == "francisco" and data["canonical_seat"].endswith("francisco.empirica-consult")
    assert data["project_id"] == "p-1" and data["ai_id"] == "empirica-consult"  # shared keys stay


def test_only_the_per_person_keys_are_overridden(tmp_path):
    data = yaml_cache.load_project_yaml(_practice(tmp_path, {**_FRANCISCO, "project_id": "evil", "ai_id": "x"}))
    assert data["project_id"] == "p-1" and data["ai_id"] == "empirica-consult"


def test_without_a_local_file_nothing_changes(tmp_path):
    assert yaml_cache.load_project_yaml(_practice(tmp_path))["tenant_slug"] == "david"


def test_the_cached_tracked_file_is_not_mutated(tmp_path):
    root = _practice(tmp_path, _FRANCISCO)
    yaml_cache.load_project_yaml(root)
    raw = yaml_cache.load_yaml_cached(root / ".empirica" / "project.yaml")
    assert raw["tenant_slug"] == "david"


# --- the writer: setup and the session-start heal put a person's seat where it belongs ---


def _persist(root, **kw):
    from empirica.cli.command_handlers.setup_claude_code import _persist_tenant_metadata

    return _persist_tenant_metadata(root, **{"org_id": "org-c", "tenant_slug": None, "mesh_id_prefix": None, **kw})


def _read(root, name):
    path = root / ".empirica" / name
    return yaml.safe_load(path.read_text()) if path.exists() else None


def test_a_second_person_in_a_shared_repo_writes_their_seat_locally(tmp_path):
    root = _practice(tmp_path)  # tracked file names david
    assert _persist(root, tenant_slug="francisco", mesh_id_prefix="empirica-consulting.francisco") is True
    tracked, local = _read(root, "project.yaml"), _read(root, "project.local.yaml")
    assert tracked["tenant_slug"] == "david" and tracked["canonical_seat"].endswith("david.empirica-consult")
    assert local == {"tenant_slug": "francisco", "mesh_id_prefix": "empirica-consulting.francisco",
                     "canonical_seat": "empirica-consulting.francisco.empirica-consult"}  # fmt: skip


def test_with_a_local_file_the_owners_keys_go_there_too(tmp_path):
    root = _practice(tmp_path, {"tenant_slug": "david"})
    _persist(root, tenant_slug="david", mesh_id_prefix="empirica-consulting.david2")
    assert _read(root, "project.local.yaml")["mesh_id_prefix"] == "empirica-consulting.david2"
    assert _read(root, "project.yaml")["mesh_id_prefix"] == "empirica-consulting.david"


def test_a_single_owner_practice_writes_the_tracked_file_as_before(tmp_path):
    emp = tmp_path / ".empirica"
    emp.mkdir()
    (emp / "project.yaml").write_text(yaml.safe_dump({"project_id": "p", "ai_id": "solo"}))
    assert _persist(tmp_path, tenant_slug="david", mesh_id_prefix="org-x.david") is True
    tracked = _read(tmp_path, "project.yaml")
    assert tracked["tenant_slug"] == "david" and tracked["canonical_seat"] == "org-x.david.solo"
    assert _read(tmp_path, "project.local.yaml") is None


def test_a_shared_repo_never_flips_the_tracked_org(tmp_path):
    root = _practice(tmp_path)
    _persist(root, org_id="org-other", tenant_slug="francisco", mesh_id_prefix="empirica-consulting.francisco")
    assert _read(root, "project.yaml")["org_id"] == "org-c"


def test_the_session_start_heal_counts_a_local_seat(tmp_path, monkeypatch):
    """Fast path: a seat in project.local.yaml is a seat; the heal must not ask Cortex or write the tracked file."""
    import importlib.util
    import sys
    from pathlib import Path

    hook = (
        Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "session-init.py"
    )
    spec = importlib.util.spec_from_file_location("session_init_overlay", hook)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["session_init_overlay"] = mod
    spec.loader.exec_module(mod)
    root = tmp_path
    emp = root / ".empirica"
    emp.mkdir()
    (emp / "project.yaml").write_text(yaml.safe_dump({"project_id": "p-1", "ai_id": "empirica-consult"}))
    (emp / "project.local.yaml").write_text(yaml.safe_dump(_FRANCISCO))
    before = (emp / "project.yaml").read_text()
    monkeypatch.setattr("empirica.cli.command_handlers.setup_claude_code._persist_tenant_metadata",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("heal ran")))  # fmt: skip
    mod._heal_mesh_metadata_at_init(str(root))
    assert (emp / "project.yaml").read_text() == before

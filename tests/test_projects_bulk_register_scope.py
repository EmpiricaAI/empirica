"""Tests for `projects-bulk-register` source-selection (1.9.6 redesign).

Sources from `~/.empirica/registry.yaml` by default. Falls back to
discovered_projects.yaml with `--from-discovered`. Explicit `--from <path>`
overrides both.

The pre-1.9.6 `--only-existing` flag + Cortex /v1/collections intersection
logic was removed: registry.yaml IS the user's curated subset, so the
intersection happens at curation time, not at command time.

`--force-metadata-update` still exists for Cortex-side safe-update of
existing rows.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch


def _make_args(*, manifest_path=None, from_discovered=False, dry_run=False, force_metadata_update=False):
    return SimpleNamespace(
        manifest_path=manifest_path,
        from_discovered=from_discovered,
        dry_run=dry_run,
        force_metadata_update=force_metadata_update,
        cortex_url="https://cortex.example.com",
        api_key="sk-test",
        timeout=10.0,
        output="json",
        includes=None,
        excludes=None,
    )


def _registry(projects):
    return {"version": 1, "projects": projects}


# ─── Source selection ─────────────────────────────────────────────────


def test_default_sources_from_registry_yaml():
    """No flags → reads registry.yaml, NOT discovered_projects.yaml."""
    from empirica.cli.command_handlers import projects_commands

    fake_registry = _registry(
        [
            {"project_id": "alpha", "slug": "alpha", "name": "alpha", "path": "/tmp/alpha", "repo_url": None},
            {"project_id": "beta", "slug": "beta", "name": "beta", "path": "/tmp/beta", "repo_url": "https://x/beta"},
        ]
    )

    posted: list[dict] = []

    def fake_register(project, *args, **kwargs):
        posted.append(project)
        return {"name": project["name"], "outcome": "registered", "status": 200}

    with (
        patch("empirica.api.registry.load_registry", return_value=fake_registry),
        patch.object(projects_commands, "load_manifest") as mock_legacy,
        patch.object(projects_commands, "_register_one_project", side_effect=fake_register),
    ):
        projects_commands.handle_projects_bulk_register_command(_make_args())
        mock_legacy.assert_not_called()  # legacy manifest path must NOT be hit

    assert {p["name"] for p in posted} == {"alpha", "beta"}


def test_from_discovered_sources_from_legacy_manifest():
    """--from-discovered → reads discovered_projects.yaml, NOT registry.yaml."""
    from empirica.cli.command_handlers import projects_commands

    fake_manifest = {
        "projects": [
            {"name": "gamma", "path": "/tmp/gamma", "repo_url": None},
            {"name": "delta", "path": "/tmp/delta", "repo_url": None},
        ],
    }

    posted: list[dict] = []

    def fake_register(project, *args, **kwargs):
        posted.append(project)
        return {"name": project["name"], "outcome": "registered", "status": 200}

    with (
        patch.object(projects_commands, "load_manifest", return_value=fake_manifest),
        patch("empirica.api.registry.load_registry") as mock_registry,
        patch.object(projects_commands, "_register_one_project", side_effect=fake_register),
    ):
        projects_commands.handle_projects_bulk_register_command(
            _make_args(from_discovered=True),
        )
        mock_registry.assert_not_called()

    assert {p["name"] for p in posted} == {"gamma", "delta"}


def test_empty_registry_bails_with_hint():
    """No projects in registry.yaml → print hint, don't POST anything."""
    from empirica.cli.command_handlers import projects_commands

    posted: list[dict] = []

    def fake_register(project, *args, **kwargs):
        posted.append(project)
        return {"name": project["name"], "outcome": "registered", "status": 200}

    with (
        patch("empirica.api.registry.load_registry", return_value=_registry([])),
        patch.object(projects_commands, "_register_one_project", side_effect=fake_register),
    ):
        projects_commands.handle_projects_bulk_register_command(_make_args())

    assert posted == []


def test_registry_slug_preferred_over_name():
    """registry.yaml carries both slug and name; bulk-register uses slug
    as the wire `name` (matches projects-discover's manifest convention)."""
    from empirica.cli.command_handlers import projects_commands

    fake_registry = _registry(
        [
            {
                "project_id": "748a-uuid",
                "slug": "empirica-cortex",
                "name": "empirica-cortex",
                "path": "/tmp/x",
                "repo_url": "https://github.com/EmpiricaAI/empirica-cortex",
            },
        ]
    )

    posted: list[dict] = []

    def fake_register(project, *args, **kwargs):
        posted.append(project)
        return {"name": project["name"], "outcome": "registered", "status": 200}

    with (
        patch("empirica.api.registry.load_registry", return_value=fake_registry),
        patch.object(projects_commands, "_register_one_project", side_effect=fake_register),
    ):
        projects_commands.handle_projects_bulk_register_command(_make_args())

    assert len(posted) == 1
    assert posted[0]["name"] == "empirica-cortex"
    assert posted[0]["repo_url"] == "https://github.com/EmpiricaAI/empirica-cortex"


# ─── Dry-run + force-metadata-update ────────────────────────────────


def test_dry_run_no_cortex_round_trip(capsys):
    """Dry-run reads registry, prints summary, never touches Cortex."""
    from empirica.cli.command_handlers import projects_commands

    fake_registry = _registry(
        [
            {"project_id": "a", "slug": "a", "name": "a", "path": "/tmp/a"},
            {"project_id": "b", "slug": "b", "name": "b", "path": "/tmp/b"},
            {"project_id": "c", "slug": "c", "name": "c", "path": "/tmp/c"},
        ]
    )

    with (
        patch("empirica.api.registry.load_registry", return_value=fake_registry),
        patch.object(projects_commands, "_register_one_project") as mock_register,
        patch.object(projects_commands, "_resolve_cortex_config") as mock_resolve,
    ):
        projects_commands.handle_projects_bulk_register_command(_make_args(dry_run=True))
        # No Cortex calls at all in dry-run
        mock_register.assert_not_called()
        mock_resolve.assert_not_called()

    out = capsys.readouterr().out
    import json

    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert len(payload["results"]) == 3


def test_force_metadata_update_passes_through_to_post():
    """--force-metadata-update sets the kwarg on _register_one_project."""
    from empirica.cli.command_handlers import projects_commands

    fake_registry = _registry(
        [
            {"project_id": "a", "slug": "a", "name": "a", "path": "/tmp/a"},
        ]
    )

    posted: list[tuple[dict, bool]] = []

    def fake_register(project, *args, **kwargs):
        posted.append((project, kwargs.get("force_metadata_update", False)))
        return {"name": project["name"], "outcome": "registered", "status": 200}

    with (
        patch("empirica.api.registry.load_registry", return_value=fake_registry),
        patch.object(projects_commands, "_register_one_project", side_effect=fake_register),
    ):
        projects_commands.handle_projects_bulk_register_command(
            _make_args(force_metadata_update=True),
        )

    assert len(posted) == 1
    assert posted[0][1] is True

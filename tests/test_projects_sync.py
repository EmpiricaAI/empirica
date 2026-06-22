"""Tests for `empirica projects-sync` — master verb collapsing
discover → registry upsert → Cortex POST.

Closes prop_ncitlxqewrabzheagvdkra5ahi. The verb composes existing
helpers (`discover_projects`, `_register_discovered_to_registry`,
`_register_one_project`) — no logic duplication, just orchestration +
phase-skip flags.

Surface tested:
  - Phase 1 (always): filesystem walk
  - Phase 2 (skip via --no-write/--dry-run): manifest cache + registry
  - Phase 3 (skip via --no-cortex/--no-write/--dry-run): Cortex POST
  - --dry-run preview path
  - --no-cortex offline path
  - Filter compose (--include/--exclude applied to phase 3 only)
  - Phase isolation: each phase failure doesn't kill subsequent phases
"""

from __future__ import annotations

import types
from unittest.mock import patch

from empirica.cli.command_handlers import projects_commands as pc


def _make_args(**overrides):
    defaults = {
        "roots": None,
        "max_depth": 5,
        "include_hidden": False,
        "includes": None,
        "excludes": None,
        "no_cortex": False,
        "no_write": False,
        "prune": False,
        "dry_run": False,
        "cortex_url": None,
        "api_key": None,
        "timeout": 10.0,
        "force_metadata_update": False,
        "output": "json",
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def _fake_manifest(n=3):
    return {
        "projects": [
            {"path": f"/fake/p{i}", "name": f"p{i}", "slug": f"p{i}", "repo_url": f"https://x/p{i}.git"}
            for i in range(n)
        ],
    }


# ── Dry-run: discovers, but writes NOTHING and POSTs NOTHING ──────────


def test_dry_run_walks_filesystem_but_writes_nothing(capsys, tmp_path):
    """--dry-run: phase 1 runs, phases 2+3 skipped, nothing persisted."""
    args = _make_args(dry_run=True)
    with (
        patch.object(pc, "discover_projects", return_value=_fake_manifest(5)),
        patch.object(pc, "write_manifest") as mock_write,
        patch.object(pc, "_register_discovered_to_registry") as mock_reg,
        patch.object(pc, "_register_one_project") as mock_post,
    ):
        pc.handle_projects_sync_command(args)

    out = capsys.readouterr()
    assert "discovered" in out.out
    assert "5" in out.out  # 5 discovered

    # None of the side-effect helpers should have fired
    mock_write.assert_not_called()
    mock_reg.assert_not_called()
    mock_post.assert_not_called()


# ── --no-write: full preview, no side effects ─────────────────────────


def test_no_write_skips_all_persistence_phases(capsys):
    """--no-write: same as dry-run — discover-only preview."""
    args = _make_args(no_write=True)
    with (
        patch.object(pc, "discover_projects", return_value=_fake_manifest(2)),
        patch.object(pc, "write_manifest") as mock_write,
        patch.object(pc, "_register_discovered_to_registry") as mock_reg,
    ):
        pc.handle_projects_sync_command(args)

    mock_write.assert_not_called()
    mock_reg.assert_not_called()


# ── --no-cortex: registry written, Cortex POST skipped ────────────────


def test_no_cortex_writes_registry_but_skips_cortex(capsys):
    """--no-cortex: full discover + registry upsert, no HTTP calls."""
    args = _make_args(no_cortex=True)
    reg_summary = {"added": 2, "updated": 1, "pruned": 0, "total": 3}
    with (
        patch.object(pc, "discover_projects", return_value=_fake_manifest(3)),
        patch.object(pc, "write_manifest"),
        patch.object(pc, "_register_discovered_to_registry", return_value=reg_summary) as mock_reg,
        patch.object(pc, "_resolve_cortex_config") as mock_cortex,
    ):
        pc.handle_projects_sync_command(args)

    mock_reg.assert_called_once()
    # Cortex config never resolved — phase 3 short-circuited
    mock_cortex.assert_not_called()
    out = capsys.readouterr()
    payload = out.out
    # Summary should report registry stats
    assert "2" in payload  # added
    # Should mention cortex was skipped (in JSON: phases_skipped includes cortex_post)
    assert "cortex_post" in payload


# ── --prune: passed through to registry upsert ────────────────────────


def test_prune_flag_passes_through_to_registry(capsys):
    args = _make_args(no_cortex=True, prune=True)
    reg_summary = {"added": 0, "updated": 5, "pruned": 3, "total": 5}
    with (
        patch.object(pc, "discover_projects", return_value=_fake_manifest(5)),
        patch.object(pc, "write_manifest"),
        patch.object(pc, "_register_discovered_to_registry", return_value=reg_summary) as mock_reg,
    ):
        pc.handle_projects_sync_command(args)

    mock_reg.assert_called_once()
    # The prune kwarg made the trip
    assert mock_reg.call_args.kwargs.get("prune") is True


# ── Filters apply to Cortex POST only, not discovery/registry ──────────


def test_filter_includes_apply_to_cortex_post_phase(capsys):
    """--include/--exclude filter what gets POSTed to Cortex but don't
    change what's discovered or written to registry.yaml."""
    args = _make_args(
        includes=["^keep-"],  # only post projects whose name starts with keep-
        cortex_url="http://cortex.test",
        api_key="ctx_test",
    )
    reg_summary = {"added": 4, "updated": 0, "pruned": 0, "total": 4}
    # All 4 discovered, registry gets all 4, but only 2 match the filter
    discovered = {
        "projects": [
            {"path": "/p/keep-a", "name": "keep-a", "slug": "keep-a"},
            {"path": "/p/keep-b", "name": "keep-b", "slug": "keep-b"},
            {"path": "/p/drop-c", "name": "drop-c", "slug": "drop-c"},
            {"path": "/p/drop-d", "name": "drop-d", "slug": "drop-d"},
        ],
    }
    registry_projects = discovered["projects"]
    with (
        patch.object(pc, "discover_projects", return_value=discovered),
        patch.object(pc, "write_manifest"),
        patch.object(pc, "_register_discovered_to_registry", return_value=reg_summary),
        patch.object(pc, "_resolve_cortex_config", return_value=("http://cortex.test", "ctx_test")),
        patch.object(pc, "_load_projects_for_register", return_value=registry_projects),
        patch.object(
            pc,
            "_register_one_project",
            return_value={"name": "x", "outcome": "registered", "status": 200, "reason": ""},
        ) as mock_post,
    ):
        pc.handle_projects_sync_command(args)

    # 2 of 4 matched filter → only those 2 POSTed
    assert mock_post.call_count == 2
    posted_names = [c.args[0]["name"] for c in mock_post.call_args_list]
    assert all(n.startswith("keep-") for n in posted_names)


# ── Phase 2 failure doesn't crash phase 3 (resilience) ─────────────────


def test_registry_failure_skips_cortex_with_explicit_signal(capsys):
    """When registry upsert raises, the handler reports the failure and
    skips Cortex POST rather than charging ahead with possibly-stale data."""
    args = _make_args()
    with (
        patch.object(pc, "discover_projects", return_value=_fake_manifest(2)),
        patch.object(pc, "write_manifest"),
        patch.object(pc, "_register_discovered_to_registry", side_effect=RuntimeError("registry boom")),
        patch.object(pc, "_resolve_cortex_config") as mock_cortex,
    ):
        pc.handle_projects_sync_command(args)

    # Cortex POST phase didn't run (registry failed first)
    mock_cortex.assert_not_called()
    out = capsys.readouterr()
    assert "registry boom" in out.err or "Registry upsert failed" in out.err


# ── Cortex config missing returns clean error, not exception ──────────


def test_missing_cortex_config_returns_clean_error(capsys):
    """When CORTEX_REMOTE_URL / CORTEX_API_KEY missing, phase 3 reports
    what's missing without crashing the verb."""
    args = _make_args()
    reg_summary = {"added": 1, "updated": 0, "pruned": 0, "total": 1}
    with (
        patch.object(pc, "discover_projects", return_value=_fake_manifest(1)),
        patch.object(pc, "write_manifest"),
        patch.object(pc, "_register_discovered_to_registry", return_value=reg_summary),
        patch.object(pc, "_resolve_cortex_config", return_value=(None, None)),
    ):
        pc.handle_projects_sync_command(args)

    err = capsys.readouterr().err
    assert "Cortex configuration missing" in err
    assert "CORTEX_REMOTE_URL" in err
    assert "CORTEX_API_KEY" in err


# ── Default (no flags) runs the full pipeline ─────────────────────────


def test_default_runs_full_pipeline(capsys):
    """No flags: walks + writes manifest + upserts registry + POSTs Cortex."""
    args = _make_args(cortex_url="http://cortex.test", api_key="ctx_test")
    reg_summary = {"added": 1, "updated": 0, "pruned": 0, "total": 1}
    manifest = _fake_manifest(1)
    with (
        patch.object(pc, "discover_projects", return_value=manifest),
        patch.object(pc, "write_manifest") as mock_write,
        patch.object(pc, "_register_discovered_to_registry", return_value=reg_summary) as mock_reg,
        patch.object(pc, "_resolve_cortex_config", return_value=("http://cortex.test", "ctx_test")),
        patch.object(pc, "_load_projects_for_register", return_value=manifest["projects"]),
        patch.object(
            pc,
            "_register_one_project",
            return_value={"name": "p0", "outcome": "registered", "status": 200, "reason": ""},
        ) as mock_post,
    ):
        pc.handle_projects_sync_command(args)

    mock_write.assert_called_once()
    mock_reg.assert_called_once()
    mock_post.assert_called_once()


# ── Cortex POST handles empty filter result cleanly ───────────────────


def test_filters_dropping_everything_does_not_crash(capsys):
    """When --include/--exclude leave zero projects to POST, the handler
    reports zero registered + zero failed instead of erroring."""
    args = _make_args(includes=["^nomatch-"], cortex_url="http://cortex.test", api_key="ctx_test")
    reg_summary = {"added": 1, "updated": 0, "pruned": 0, "total": 1}
    with (
        patch.object(pc, "discover_projects", return_value={"projects": [{"path": "/a", "name": "a", "slug": "a"}]}),
        patch.object(pc, "write_manifest"),
        patch.object(pc, "_register_discovered_to_registry", return_value=reg_summary),
        patch.object(pc, "_resolve_cortex_config", return_value=("http://cortex.test", "ctx_test")),
        patch.object(pc, "_load_projects_for_register", return_value=[{"path": "/a", "name": "a", "slug": "a"}]),
        patch.object(pc, "_register_one_project") as mock_post,
    ):
        pc.handle_projects_sync_command(args)

    mock_post.assert_not_called()


# ── `project-sync` (singular) alias → `projects-sync` ─────────────────
# Practitioners + the extension reach for the singular `project-sync`;
# the canonical verb is plural. The alias closes that natural-language gap.


def test_project_sync_alias_parses_and_shares_args():
    """The singular alias is accepted by the parser and shares the
    projects-sync flag surface."""
    import argparse

    from empirica.cli.parsers.projects_parsers import add_projects_parsers

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_projects_parsers(subparsers)

    # Alias parses without error and shares the --dry-run/--no-cortex flags.
    args = parser.parse_args(["project-sync", "--dry-run", "--no-cortex", "--no-write"])
    assert args.command in ("project-sync", "projects-sync")
    assert args.dry_run is True and args.no_cortex is True


def test_project_sync_alias_dispatches_end_to_end():
    """Regression guard for the dispatch dict: argparse `aliases=` only
    affects parsing — `args.command` carries the invoked alias string, so
    without an explicit dispatch entry the alias fails with 'Unknown command'.
    This runs the real CLI to prove the full parse→dispatch chain works."""
    import subprocess

    result = subprocess.run(
        ["empirica", "project-sync", "--dry-run", "--no-cortex", "--no-write"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"alias dispatch failed: {result.stderr or result.stdout}"
    # Dispatched to the projects-sync handler (dry-run preview output — the
    # human status lines print to stderr; the JSON path would be stdout).
    combined = result.stdout + result.stderr
    assert "DRY RUN" in combined or "Discovered" in combined

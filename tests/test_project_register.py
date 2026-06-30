"""Tests for the V1.5 single-verb `empirica project-register [PATH]`.

The verb is the atomic discover-one + register-one pair that extension's
Discover/Register surface design (prop_apevka5iwj) is built around. It
replaces the brittle chain of ``projects-discover --register NAME &&
projects-bulk-register --include NAME`` with one verb optimised for the
copy-prompt-to-AI UX.

Coverage:
1. Missing .empirica/project.yaml → exit 1 with actionable hint.
2. Missing project_id in project.yaml → exit 1 pointing at project-init.
3. Happy path with --no-cortex writes workspace.db (dual) + registry.yaml.
4. workspace.db carries entity_registry row (T1 dual-write).
5. registry.yaml carries the project entry with project_id + slug + path.
6. Re-running with --no-cortex is idempotent.
7. Cortex POST happy path: 201 → ok=True, outcome=registered.
8. Cortex POST 409 already_exists → ok=True, outcome=already_registered.
9. Cortex POST returns divergent project_id → diverged=True + local_project_id surfaced.
10. Cortex POST 500 → ok=False, exit 2 (local writes still landed).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

# --- Helpers ---


def _seed_project(
    tmp_path: Path,
    *,
    project_id: str | None = "11111111-1111-1111-1111-111111111111",
    name: str = "empirica-test-project",
) -> Path:
    """Create a tmp project dir with .empirica/project.yaml."""
    project_dir = tmp_path / "src" / name
    (project_dir / ".empirica").mkdir(parents=True, exist_ok=True)
    yaml_data = {
        "version": "2.0",
        "name": name,
        "type": "software",
        "description": f"Test project: {name}",
    }
    if project_id is not None:
        yaml_data["project_id"] = project_id
    (project_dir / ".empirica" / "project.yaml").write_text(
        yaml.dump(yaml_data, default_flow_style=False),
        encoding="utf-8",
    )
    return project_dir


def _make_args(path: str, **overrides) -> SimpleNamespace:
    defaults = {
        "path": path,
        "no_cortex": True,
        "skip_user_link": False,
        "force_metadata_update": False,
        "cortex_url": None,
        "api_key": None,
        "timeout": 10.0,
        "output": "json",
        "verbose": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _override_home(tmp_path: Path):
    """Patch Path.home() to a tmp_path across all the modules that read it.

    Note: api/registry.py computes DEFAULT_REGISTRY_PATH at module-import
    time, so patching Path.home() there is too late. We patch the resolved
    constant directly instead.
    """
    registry_path = tmp_path / ".empirica" / "registry.yaml"
    return [
        patch("empirica.cli.command_handlers.workspace_init.Path.home", return_value=tmp_path),
        patch("empirica.api.registry.DEFAULT_REGISTRY_PATH", registry_path),
        patch("empirica.data.repositories.workspace_db.Path.home", return_value=tmp_path),
    ]


def _run_register(tmp_path: Path, args: SimpleNamespace, capsys):
    """Run handle_project_register_command under redirected $HOME."""
    from empirica.cli.command_handlers.projects_commands import (
        handle_project_register_command,
    )

    patches = _override_home(tmp_path)
    for p in patches:
        p.start()
    try:
        try:
            handle_project_register_command(args)
            exit_code = 0
        except SystemExit as e:
            exit_code = e.code if e.code is not None else 0
    finally:
        # Stop in reverse order — multiple patches on the same Path.home
        # capture their "original" as the prior mock; forward-order stop
        # then leaves the first mock installed, polluting Path.home for
        # subsequent tests (notably test_session_init_canonical_loops).
        for p in reversed(patches):
            p.stop()
    out, err = capsys.readouterr()
    payload = None
    if args.output == "json" and out.strip():
        try:
            payload = json.loads(out)
        except json.JSONDecodeError:
            pass
    return exit_code, payload, out, err


# --- Error paths ---


def test_missing_empirica_dir_exits_with_hint(tmp_path, capsys):
    empty = tmp_path / "not-a-project"
    empty.mkdir()
    exit_code, payload, _out, _err = _run_register(
        tmp_path,
        _make_args(str(empty)),
        capsys,
    )
    assert exit_code == 1
    assert payload is not None
    assert payload["ok"] is False
    assert "No .empirica/project.yaml" in payload["error"]
    assert "project-init" in payload["hint"]


def test_missing_project_id_in_yaml_exits(tmp_path, capsys):
    project = _seed_project(tmp_path, project_id=None)
    exit_code, payload, _out, _err = _run_register(
        tmp_path,
        _make_args(str(project)),
        capsys,
    )
    assert exit_code == 1
    assert payload["ok"] is False
    assert "no project_id" in payload["error"]
    assert "project-init" in payload["hint"]


def test_nonexistent_path_exits_with_hint(tmp_path, capsys):
    nowhere = tmp_path / "definitely-not-there"
    exit_code, payload, _out, _err = _run_register(
        tmp_path,
        _make_args(str(nowhere)),
        capsys,
    )
    assert exit_code == 1
    assert payload["ok"] is False
    assert "does not exist" in payload["error"]


# --- Happy path: --no-cortex ---


def test_no_cortex_writes_workspace_db_and_registry(tmp_path, capsys):
    pid = "22222222-3333-4444-5555-666666666666"
    project = _seed_project(tmp_path, project_id=pid, name="empirica-foo")
    exit_code, payload, _out, _err = _run_register(
        tmp_path,
        _make_args(str(project), no_cortex=True),
        capsys,
    )
    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["local"]["project_id"] == pid
    assert payload["local"]["workspace_db"] is True
    assert payload["local"]["registry_yaml"] is True
    assert payload["cortex"]["skipped"] is True

    # workspace.db has both rows
    db = tmp_path / ".empirica/workspace/workspace.db"
    assert db.exists()
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT id, name FROM global_projects WHERE id = ?", (pid,))
    gp = cur.fetchone()
    assert gp is not None
    assert gp[1] == "empirica-foo"
    cur.execute(
        "SELECT entity_id, display_name FROM entity_registry WHERE entity_type = 'project' AND entity_id = ?",
        (pid,),
    )
    er = cur.fetchone()
    assert er is not None
    assert er[0] == pid
    assert er[1] == "empirica-foo"
    conn.close()

    # registry.yaml has the entry
    reg = tmp_path / ".empirica/registry.yaml"
    assert reg.exists()
    reg_data = yaml.safe_load(reg.read_text())
    entries = [p for p in reg_data["projects"] if p["project_id"] == pid]
    assert len(entries) == 1
    assert entries[0]["name"] == "empirica-foo"
    assert entries[0]["path"] == str(project)


def test_no_cortex_idempotent_rerun(tmp_path, capsys):
    """Second --no-cortex run with same args succeeds and produces the same
    counts in both tables (no duplicate rows)."""
    pid = "33333333-4444-5555-6666-777777777777"
    project = _seed_project(tmp_path, project_id=pid, name="empirica-bar")
    args = _make_args(str(project), no_cortex=True)

    code1, _, _, _ = _run_register(tmp_path, args, capsys)
    code2, _, _, _ = _run_register(tmp_path, args, capsys)
    assert code1 == 0 and code2 == 0

    db = tmp_path / ".empirica/workspace/workspace.db"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM global_projects WHERE id = ?", (pid,))
    assert cur.fetchone()[0] == 1
    cur.execute(
        "SELECT COUNT(*) FROM entity_registry WHERE entity_type = 'project' AND entity_id = ?",
        (pid,),
    )
    assert cur.fetchone()[0] == 1
    conn.close()

    reg_data = yaml.safe_load((tmp_path / ".empirica/registry.yaml").read_text())
    assert sum(1 for p in reg_data["projects"] if p["project_id"] == pid) == 1


# --- Cortex POST paths (mock _post_project) ---


def _cortex_args(project_path: str) -> SimpleNamespace:
    return _make_args(
        project_path,
        no_cortex=False,
        cortex_url="https://example.com",
        api_key="ctx_test_key",
        skip_user_link=True,  # avoid the second POST in tests
    )


def test_cortex_201_registered(tmp_path, capsys):
    pid = "44444444-5555-6666-7777-888888888888"
    project = _seed_project(tmp_path, project_id=pid, name="empirica-baz")
    args = _cortex_args(str(project))

    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(201, {"project_id": pid, "name": "empirica-baz"}),
    ):
        exit_code, payload, _out, _err = _run_register(tmp_path, args, capsys)
    assert exit_code == 0
    assert payload["cortex"]["ok"] is True
    assert payload["cortex"]["outcome"] == "registered"
    assert payload["cortex"]["status"] == 201
    assert payload["cortex"]["diverged"] is False


def test_cortex_409_already_registered(tmp_path, capsys):
    pid = "55555555-6666-7777-8888-999999999999"
    project = _seed_project(tmp_path, project_id=pid, name="empirica-qux")
    args = _cortex_args(str(project))

    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(409, {"project_id": pid, "name": "empirica-qux"}),
    ):
        exit_code, payload, _out, _err = _run_register(tmp_path, args, capsys)
    assert exit_code == 0
    assert payload["cortex"]["ok"] is True
    assert payload["cortex"]["outcome"] == "already_registered"
    assert payload["cortex"]["status"] == 409


def test_cortex_divergent_project_id_surfaced(tmp_path, capsys):
    """When cortex returns a different project_id than the local UUID,
    diverged=True and local_project_id is exposed so callers can surface
    the SER ser_542199e3 Break 1 diagnostic."""
    local_pid = "66666666-aaaa-bbbb-cccc-dddddddddddd"
    cortex_pid = "99999999-eeee-ffff-0000-111111111111"
    project = _seed_project(tmp_path, project_id=local_pid, name="empirica-mesh-test")
    args = _cortex_args(str(project))

    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(201, {"project_id": cortex_pid}),
    ):
        exit_code, payload, _out, _err = _run_register(tmp_path, args, capsys)
    assert exit_code == 0
    assert payload["cortex"]["diverged"] is True
    assert payload["cortex"]["project_id"] == cortex_pid
    assert payload["cortex"]["local_project_id"] == local_pid


def test_cortex_500_exits_2_local_persists(tmp_path, capsys):
    """Local writes complete; cortex 500 returns exit 2 (re-runnable)."""
    pid = "77777777-2222-3333-4444-555555555555"
    project = _seed_project(tmp_path, project_id=pid, name="empirica-cortex-fail")
    args = _cortex_args(str(project))

    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(500, None),
    ):
        exit_code, payload, _out, _err = _run_register(tmp_path, args, capsys)
    assert exit_code == 2
    assert payload["ok"] is True  # outer result is "we tried"
    assert payload["local"]["workspace_db"] is True  # local landed
    assert payload["cortex"]["ok"] is False
    assert payload["cortex"]["status"] == 500

    # workspace.db row still there — re-run safe
    db = tmp_path / ".empirica/workspace/workspace.db"
    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM global_projects WHERE id = ?", (pid,))
    assert cur.fetchone()[0] == 1
    conn.close()


def test_cortex_400_owner_conflict_surfaces_rekey_hint(tmp_path, capsys):
    """A cloned/foreign committed project_id → cortex 400 'different owner' →
    owner_conflict outcome with an actionable re-key hint, the foreign pid is
    NOT user-linked, and exit 2 (re-runnable after re-key)."""
    pid = "88888888-3333-4444-5555-666666666666"
    project = _seed_project(tmp_path, project_id=pid, name="empirica-cloned")
    args = _cortex_args(str(project))

    with (
        patch(
            "empirica.cli.command_handlers.projects_commands._post_project",
            return_value=(
                400,
                {
                    "error": "project_id is already registered to a different owner; regenerate the local project_id or unregister the existing row"
                },
            ),
        ),
        patch("empirica.cli.command_handlers.projects_commands._link_user_to_project") as mock_link,
    ):
        exit_code, payload, _out, _err = _run_register(tmp_path, args, capsys)

    assert exit_code == 2
    assert payload["cortex"]["ok"] is False
    assert payload["cortex"]["outcome"] == "owner_conflict"
    assert payload["cortex"]["status"] == 400
    assert "regenerate" in (payload["cortex"].get("hint") or "").lower()
    # the foreign pid must NEVER be linked to the caller (the leak vector)
    mock_link.assert_not_called()


def test_is_owner_conflict_matcher():
    """The 400-owner-conflict matcher keys on cortex's phrase across body fields."""
    from empirica.cli.command_handlers.projects_commands import _is_owner_conflict

    assert _is_owner_conflict(400, {"error": "already registered to a different owner"}) is True
    assert _is_owner_conflict(400, {"message": "project_id owned by a different owner"}) is True
    assert _is_owner_conflict(400, {"detail": "bad request: missing name"}) is False  # other 400s
    assert _is_owner_conflict(409, {"error": "different owner"}) is False  # only 400
    assert _is_owner_conflict(400, None) is False

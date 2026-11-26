"""Tests for `empirica projects-unregister` — soft archive + --purge."""

from __future__ import annotations

import json
import types
from unittest.mock import patch

from empirica.cli.command_handlers.projects_commands import (
    handle_projects_unregister_command,
)


def _make_args(**overrides):
    defaults = {
        "project_id": "p-test-uuid",
        "slug": None,
        "purge": False,
        "confirm": False,
        "cortex_url": "https://cortex.test",
        "api_key": "test-key",
        "timeout": 5.0,
        "output": "json",
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


# ─── happy paths ──────────────────────────────────────────────────────


def test_soft_archive_returns_archived_outcome(capsys):
    args = _make_args()
    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(200, {"ok": True}),
    ):
        handle_projects_unregister_command(args)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["outcome"] == "archived"
    assert out["status_code"] == 200
    assert out["purge"] is False


def test_purge_with_confirm_returns_purged_outcome(capsys):
    args = _make_args(purge=True, confirm=True)
    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(200, {"ok": True}),
    ):
        handle_projects_unregister_command(args)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["outcome"] == "purged"
    assert out["purge"] is True


def test_already_archived_idempotent_409_ok(capsys):
    """409 (already archived) is treated as success — idempotent operation."""
    args = _make_args()
    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(409, {"reason": "already archived"}),
    ):
        handle_projects_unregister_command(args)
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    assert out["outcome"] == "already_archived"


# ─── guards ───────────────────────────────────────────────────────────


def test_purge_without_confirm_exits_2(capsys):
    args = _make_args(purge=True, confirm=False)
    try:
        handle_projects_unregister_command(args)
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert e.code == 2
    err = capsys.readouterr().err
    assert "--purge is irreversible" in err
    assert "--confirm" in err


def test_no_project_identification_exits_2(capsys, monkeypatch, tmp_path):
    """When --project-id, --slug, and .empirica/project.yaml all absent,
    exit 2 with clear message."""
    monkeypatch.chdir(tmp_path)
    args = _make_args(project_id=None, slug=None)
    try:
        handle_projects_unregister_command(args)
        raise AssertionError("expected SystemExit")
    except SystemExit as e:
        assert e.code == 2
    err = capsys.readouterr().err
    assert "project not identified" in err


def test_no_cortex_config_exits_2(capsys):
    args = _make_args(cortex_url=None, api_key=None)
    with patch(
        "empirica.cli.command_handlers.projects_commands._resolve_cortex_config",
        return_value=(None, None),
    ):
        try:
            handle_projects_unregister_command(args)
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert e.code == 2
    err = capsys.readouterr().err
    assert "no Cortex sign-in" in err and "auth login" in err


# ─── 404 / error paths ────────────────────────────────────────────────


def test_404_not_found_exits_1(capsys):
    args = _make_args()
    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(404, None),
    ):
        try:
            handle_projects_unregister_command(args)
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert e.code == 1


def test_resolves_project_id_from_yaml(capsys, monkeypatch, tmp_path):
    """When --project-id absent but .empirica/project.yaml has one,
    extract it."""
    empirica_dir = tmp_path / ".empirica"
    empirica_dir.mkdir()
    (empirica_dir / "project.yaml").write_text(
        "project_id: p-from-yaml\nai_id: test\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    args = _make_args(project_id=None)

    captured_payload = {}

    def _capture(url, path, payload, key, timeout):
        captured_payload.update(payload)
        return (200, {"ok": True})

    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        side_effect=_capture,
    ):
        handle_projects_unregister_command(args)

    assert captured_payload.get("project_id") == "p-from-yaml"

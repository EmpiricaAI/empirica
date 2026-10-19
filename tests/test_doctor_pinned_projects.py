"""Doctor shows which cortex projects are pinned, and which pinned ones this box does not carry.

Asked by empirica.david.empirica-cortex for empirica-nle (prop_ilvp7yhvrbc75gfbzksp6hqq5y), then refined once the extension
answered (prop_zi6v7hqtivdo7ec6t23ktm2nse): a pin is membership of the ACTIVE SET and records no reason, so "not pinned" cannot
tell deliberately excluded from never selected. Doctor therefore says "pinned" and "not in the active set", warns about a pinned
project this box has not registered, and never words an unpinned one as excluded.

No test touches the network, the home directory or the real workspace.db: the HTTP call is replaced and the workspace database
is redirected through EMPIRICA_WORKSPACE_DB.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from empirica.cli.command_handlers import doctor
from empirica.cli.command_handlers.doctor import FAIL, PASS, SKIP, WARN
from empirica.data.repositories.workspace_db import WorkspaceDBRepository

PINNED_HERE = "11111111-1111-1111-1111-111111111111"
PINNED_ELSEWHERE = "22222222-2222-2222-2222-222222222222"
UNPINNED = "33333333-3333-3333-3333-333333333333"
ARCHIVED_PINNED = "44444444-4444-4444-4444-444444444444"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(tmp_path / "workspace.db"))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.example")
    monkeypatch.setenv("CORTEX_API_KEY", "ctx_test")
    with WorkspaceDBRepository.open(ensure_schema=True) as repo:
        repo.upsert_project(project_id=PINNED_HERE, name="carried", trajectory_path=str(tmp_path / "a" / ".empirica"))
    return tmp_path


def _projects(*rows):
    return json.dumps({"projects": list(rows)})


def _run(body, status=200):
    with patch("empirica.cli.command_handlers.doctor._http_get", return_value=(status, body)):
        return doctor.check_pinned_projects()


def test_a_pinned_project_this_box_has_not_registered_is_a_warning_that_names_it(workspace):
    result = _run(
        _projects(
            {"id": PINNED_HERE, "name": "carried", "pinned": True},
            {"id": PINNED_ELSEWHERE, "name": "missing-here", "display_name": "Missing Here", "pinned": True},
        )
    )
    assert result.status == WARN
    assert "Missing Here" in result.detail
    assert result.data["pinned_missing"] == [PINNED_ELSEWHERE]


def test_everything_pinned_is_registered_here_passes(workspace):
    result = _run(_projects({"id": PINNED_HERE, "name": "carried", "pinned": True}))
    assert result.status == PASS
    assert result.data["pinned_missing"] == []


def test_an_unpinned_project_is_never_called_excluded_and_is_not_a_gap(workspace):
    result = _run(
        _projects(
            {"id": PINNED_HERE, "name": "carried", "pinned": True},
            {"id": UNPINNED, "name": "never-selected", "pinned": False},
        )
    )
    assert result.status == PASS, "an unpinned project missing locally is not a registry gap"
    text = (result.detail + " " + (result.hint or "")).lower()
    assert "not in the active set" in text
    for forbidden in ("excluded", "deliberately", "ignored", "skipped on purpose"):
        assert forbidden not in text, f"unpinned must not be worded as {forbidden!r}: the pin records no intent"
    assert result.data["unpinned_count"] == 1


def test_an_archived_pinned_project_is_not_expected_here(workspace):
    result = _run(
        _projects(
            {"id": PINNED_HERE, "name": "carried", "pinned": True},
            {"id": ARCHIVED_PINNED, "name": "old", "pinned": True, "is_archived": True},
        )
    )
    assert result.status == PASS and result.data["pinned_missing"] == []


def test_a_payload_without_the_pinned_flag_does_not_invent_one(workspace):
    """Older cortex versions: say so, do not guess that everything is unpinned or everything is pinned."""
    result = _run(_projects({"id": PINNED_HERE, "name": "carried"}))
    assert result.status == SKIP
    assert "pinned" in result.detail


def test_without_cortex_credentials_it_skips(workspace, monkeypatch):
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    with patch("empirica.config.credentials_loader.get_credentials_loader", side_effect=Exception("no loader")):
        assert doctor.check_pinned_projects().status == SKIP


def test_an_unreachable_cortex_is_a_warning_not_a_pass(workspace):
    assert _run("", status=-1).status == WARN


def test_a_server_error_is_a_warning(workspace):
    assert _run("boom", status=503).status == WARN


def test_names_are_listed_but_no_secret_bearing_field_is_ever_copied_into_the_result(workspace):
    row = {
        "id": PINNED_ELSEWHERE,
        "name": "missing-here",
        "pinned": True,
        "forgejo_token_encrypted": "SECRET-TOKEN-VALUE",
        "forgejo_deploy_key_encrypted": "SECRET-KEY-VALUE",
    }
    result = _run(_projects(row))
    blob = json.dumps({"detail": result.detail, "fix": result.hint, "data": result.data}, default=str)
    assert "SECRET" not in blob


def test_the_drift_check_labels_the_local_project_pinned_or_not_in_the_active_set(workspace):
    import yaml

    (workspace / ".empirica").mkdir()
    (workspace / ".empirica" / "project.yaml").write_text(yaml.safe_dump({"project_id": UNPINNED}))
    body = _projects({"id": UNPINNED, "pinned": False}, {"id": PINNED_HERE, "pinned": True})
    with patch("empirica.cli.command_handlers.doctor._http_get", return_value=(200, body)):
        result = doctor.check_project_drift(workspace)
    assert result.status == PASS
    assert "not in the active set" in result.detail

    (workspace / ".empirica" / "project.yaml").write_text(yaml.safe_dump({"project_id": PINNED_HERE}))
    with patch("empirica.cli.command_handlers.doctor._http_get", return_value=(200, body)):
        result = doctor.check_project_drift(workspace)
    assert result.status == PASS and "pinned" in result.detail and "not in the active set" not in result.detail


def test_failure_status_constant_is_importable():
    """Guard against the module's status vocabulary changing under this test's feet."""
    assert FAIL and PASS and WARN and SKIP

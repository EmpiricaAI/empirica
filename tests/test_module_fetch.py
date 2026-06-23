"""Tests for ``empirica module fetch`` — the auth-gated artifact pre-step.

Covers: dry-run planning, idempotent skip (already-installed pkg / already-staged
archive), local plugin_archive copy, unconfigured-registry graceful path,
reference-only secret resolution, and the CLI handler exit codes. No real pip
install or network — the install path is mocked; archive staging uses temp files.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import yaml

from empirica.cli.command_handlers.module_commands import handle_module_fetch_command
from empirica.core.modules import executors
from empirica.core.modules.executors import _resolve_secret_ref, fetch_module
from empirica.core.modules.manifest import load_manifest


def _manifest(tmp_path, *, python_packages=None, plugin_archive=None, secrets_ref=None):
    body = {
        "name": "outreach",
        "seat_name": "empirica-outreach",
        "version": "0.4.0",
        "seat": {"import": "docs/OUTREACH_SEAT.md"},
        "artifacts": {},
    }
    if python_packages is not None:
        body["artifacts"]["python_packages"] = python_packages
    if plugin_archive is not None:
        body["artifacts"]["plugin_archive"] = plugin_archive
    if secrets_ref is not None:
        body["requires_runtime"] = {"secrets_ref": secrets_ref}
    p = tmp_path / "module.yaml"
    p.write_text(yaml.safe_dump({"empirica_module": body}))
    return load_manifest(p)


# ── secret resolution (reference-only) ──────────────────────────────────


def test_resolve_secret_env_scheme(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "s3cr3t")
    val, status = _resolve_secret_ref("env:MY_TOKEN")
    assert val == "s3cr3t" and status == "resolved"


def test_resolve_secret_env_unset(monkeypatch):
    monkeypatch.delenv("MISSING_TOKEN", raising=False)
    val, status = _resolve_secret_ref("env:MISSING_TOKEN")
    assert val is None and status == "env_unset"


def test_resolve_secret_none():
    assert _resolve_secret_ref(None) == (None, "none")


def test_resolve_secret_manager_absent(monkeypatch):
    # a scheme whose CLI is not on PATH degrades gracefully
    monkeypatch.setattr(executors.shutil, "which", lambda _c: None)
    val, status = _resolve_secret_ref("doppler://empirica/outreach")
    assert val is None and status == "manager_cli_absent"


# ── dry-run planning ────────────────────────────────────────────────────


def test_dry_run_writes_nothing(tmp_path):
    m = _manifest(tmp_path, python_packages=["definitely-not-a-real-pkg==9.9"], plugin_archive="x.tar.gz")
    staging = tmp_path / "staging"
    receipt = fetch_module(m, dry_run=True, staging_root=staging)
    assert receipt["dry_run"] is True
    assert not staging.exists()  # nothing created under dry-run
    kinds = {s["kind"]: s["status"] for s in receipt["steps"]}
    assert kinds["python_package"] == "would_install"
    assert kinds["plugin_archive"] == "unconfigured"  # not local, no registry


def test_dry_run_skips_installed_package(tmp_path):
    # pydantic is a hard dependency → always installed → skipped, not would_install
    m = _manifest(tmp_path, python_packages=["pydantic"])
    receipt = fetch_module(m, dry_run=True, staging_root=tmp_path / "s")
    assert receipt["steps"][0]["status"] == "skipped"


# ── plugin_archive staging ──────────────────────────────────────────────


def test_local_archive_is_copied(tmp_path):
    src = tmp_path / "outreach-0.4.0-plugin.tar.gz"
    src.write_bytes(b"PK\x03\x04 fake archive")
    m = _manifest(tmp_path, plugin_archive=str(src))
    staging = tmp_path / "staging"
    receipt = fetch_module(m, dry_run=False, staging_root=staging)
    assert receipt["ok"] is True
    step = receipt["steps"][0]
    assert step["status"] == "fetched" and step["detail"] == "copied_local"
    assert (staging / "outreach" / src.name).exists()


def test_archive_idempotent_skip(tmp_path):
    src = tmp_path / "plugin.tar.gz"
    src.write_bytes(b"data")
    m = _manifest(tmp_path, plugin_archive=str(src))
    staging = tmp_path / "staging"
    fetch_module(m, dry_run=False, staging_root=staging)  # first stages it
    receipt = fetch_module(m, dry_run=False, staging_root=staging)  # second is a no-op
    assert receipt["steps"][0]["status"] == "skipped"


def test_remote_archive_unconfigured(tmp_path):
    m = _manifest(tmp_path, plugin_archive="remote-only.tar.gz")
    receipt = fetch_module(m, dry_run=False, staging_root=tmp_path / "s")
    assert receipt["steps"][0]["status"] == "unconfigured"
    assert receipt["ok"] is True  # unconfigured is not an error, just nothing to do


def test_remote_archive_unresolved_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("ABSENT", raising=False)
    m = _manifest(tmp_path, plugin_archive="remote.tar.gz", secrets_ref="env:ABSENT")
    receipt = fetch_module(m, dry_run=False, staging_root=tmp_path / "s", registry_base="https://reg.example")
    step = receipt["steps"][0]
    assert step["status"] == "unresolved_secret" and receipt["ok"] is False


# ── pip install path (mocked) ───────────────────────────────────────────


def test_install_runs_pip(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(executors, "_pkg_installed", lambda spec: False)
    monkeypatch.setattr(executors, "_pip_install", lambda spec, idx: calls.append((spec, idx)) or (True, "installed"))
    m = _manifest(tmp_path, python_packages=["empirica-outreach==0.4.0"])
    receipt = fetch_module(m, dry_run=False, staging_root=tmp_path / "s", index_url="https://idx.example")
    assert receipt["steps"][0]["status"] == "installed"
    assert calls == [("empirica-outreach==0.4.0", "https://idx.example")]


def test_install_pip_error_surfaces(tmp_path, monkeypatch):
    monkeypatch.setattr(executors, "_pkg_installed", lambda spec: False)
    monkeypatch.setattr(executors, "_pip_install", lambda spec, idx: (False, "no matching distribution"))
    m = _manifest(tmp_path, python_packages=["nope==1.0"])
    receipt = fetch_module(m, dry_run=False, staging_root=tmp_path / "s")
    assert receipt["ok"] is False and receipt["steps"][0]["status"] == "error"


# ── CLI handler ─────────────────────────────────────────────────────────


def test_cli_fetch_dry_run_exit_0(tmp_path, capsys):
    m_path = tmp_path / "module.yaml"
    _manifest(tmp_path)  # writes module.yaml then overwritten below for a clean one
    m_path.write_text(
        yaml.safe_dump(
            {
                "empirica_module": {
                    "name": "outreach",
                    "seat_name": "empirica-outreach",
                    "version": "0.4.0",
                    "seat": {"import": "docs/X.md"},
                }
            }
        )
    )
    args = SimpleNamespace(
        path=str(m_path), dry_run=True, registry=None, index_url=None, staging_root=str(tmp_path / "s"), output="json"
    )
    rc = handle_module_fetch_command(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["dry_run"] is True


def test_cli_fetch_invalid_manifest_exit_1(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump({"empirica_module": {"name": "x"}}))  # missing required fields
    args = SimpleNamespace(path=str(bad), dry_run=True, output="json")
    rc = handle_module_fetch_command(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False

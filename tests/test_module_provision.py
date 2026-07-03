"""Tests for ``empirica module provision`` — the plugin layer (leg 3).

Covers the four steps — plugin-file placement, automation registration
(``empirica loop register``, kind-mapped), cortex ntfy-topic grants, and env
presence — plus dry-run planning and CLI exit codes. No real subprocess,
network, or ~/.claude writes: loop-register + grant POST are mocked; file
placement uses temp dirs.
"""

from __future__ import annotations

import json
import tarfile
from types import SimpleNamespace

import yaml

from empirica.cli.command_handlers.module_commands import handle_module_provision_command
from empirica.core.modules import executors
from empirica.core.modules.executors import provision_module
from empirica.core.modules.manifest import load_manifest


def _manifest(tmp_path, *, plugin_archive=None, automations=None, topics=None, env=None, domains=None):
    body = {
        "name": "outreach",
        "seat_name": "empirica-outreach",
        "version": "0.4.0",
        "seat": {"import": "docs/OUTREACH_SEAT.md"},
        "artifacts": {},
        "provides": {},
        "requires_runtime": {},
    }
    if plugin_archive is not None:
        body["artifacts"]["plugin_archive"] = plugin_archive
    if automations is not None:
        body["provides"]["automations"] = automations
    if domains is not None:
        body["provides"]["domains"] = domains
    if topics is not None:
        body["requires_runtime"]["topics"] = topics
    if env is not None:
        body["requires_runtime"]["env"] = env
    p = tmp_path / "module.yaml"
    p.write_text(yaml.safe_dump({"empirica_module": body}))
    return load_manifest(p)


def _staged_tar(tmp_path, name="outreach", archive="outreach-0.4.0-plugin.tar.gz"):
    """Create a staged plugin archive as ``module fetch`` would leave it."""
    staging = tmp_path / "staging"
    moddir = staging / name
    moddir.mkdir(parents=True)
    payload = tmp_path / "skill.md"
    payload.write_text("# a skill")
    tar_path = moddir / archive
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(payload, arcname="skills/devto.md")
    return staging


# ── plugin file placement ───────────────────────────────────────────────


def test_no_artifact_is_graceful(tmp_path):
    m = _manifest(tmp_path)  # no plugin_archive
    receipt = provision_module(m, dry_run=False, plugin_root=tmp_path / "plugins", staging_root=tmp_path / "s")
    assert receipt["steps"][0]["status"] == "no_artifact" and receipt["ok"] is True


def test_not_staged_reports_clearly(tmp_path):
    m = _manifest(tmp_path, plugin_archive="missing.tar.gz")
    receipt = provision_module(m, dry_run=False, plugin_root=tmp_path / "plugins", staging_root=tmp_path / "s")
    assert receipt["steps"][0]["status"] == "not_staged"


def test_staged_archive_is_extracted(tmp_path):
    staging = _staged_tar(tmp_path)
    m = _manifest(tmp_path, plugin_archive="outreach-0.4.0-plugin.tar.gz")
    plugins = tmp_path / "plugins"
    receipt = provision_module(m, dry_run=False, plugin_root=plugins, staging_root=staging)
    assert receipt["steps"][0]["status"] == "placed"
    assert (plugins / "outreach" / "skills" / "devto.md").exists()


def test_placement_idempotent_skip(tmp_path):
    staging = _staged_tar(tmp_path)
    m = _manifest(tmp_path, plugin_archive="outreach-0.4.0-plugin.tar.gz")
    plugins = tmp_path / "plugins"
    provision_module(m, dry_run=False, plugin_root=plugins, staging_root=staging)
    receipt = provision_module(m, dry_run=False, plugin_root=plugins, staging_root=staging)
    assert receipt["steps"][0]["status"] == "skipped"


# ── plugin registration — Model B (installed_plugins.json) ──────────────


def test_register_plugin_writes_entry(tmp_path):
    staging = _staged_tar(tmp_path)
    m = _manifest(tmp_path, plugin_archive="outreach-0.4.0-plugin.tar.gz")
    plugins = tmp_path / "plugins"
    receipt = provision_module(m, dry_run=False, plugin_root=plugins, staging_root=staging)
    reg_step = next(s for s in receipt["steps"] if s["kind"] == "plugin_register")
    assert reg_step["status"] == "registered"
    registry = json.loads((tmp_path / "installed_plugins.json").read_text())
    entry = registry["plugins"]["outreach@local"][0]
    assert entry["installPath"] == str(plugins / "outreach")
    assert entry["version"] == "0.4.0" and entry["isLocal"] is True


def test_register_plugin_generates_plugin_json(tmp_path):
    staging = _staged_tar(tmp_path)
    m = _manifest(tmp_path, plugin_archive="outreach-0.4.0-plugin.tar.gz")
    plugins = tmp_path / "plugins"
    provision_module(m, dry_run=False, plugin_root=plugins, staging_root=staging)
    pj = plugins / "outreach" / ".claude-plugin" / "plugin.json"
    assert pj.exists()
    data = json.loads(pj.read_text())
    assert data["name"] == "outreach" and data["version"] == "0.4.0"


def test_register_plugin_idempotent(tmp_path):
    staging = _staged_tar(tmp_path)
    m = _manifest(tmp_path, plugin_archive="outreach-0.4.0-plugin.tar.gz")
    plugins = tmp_path / "plugins"
    provision_module(m, dry_run=False, plugin_root=plugins, staging_root=staging)
    receipt = provision_module(m, dry_run=False, plugin_root=plugins, staging_root=staging)
    reg_step = next(s for s in receipt["steps"] if s["kind"] == "plugin_register")
    assert reg_step["status"] == "skipped"


def test_register_plugin_dry_run_no_write(tmp_path):
    staging = _staged_tar(tmp_path)
    m = _manifest(tmp_path, plugin_archive="outreach-0.4.0-plugin.tar.gz")
    plugins = tmp_path / "plugins"
    receipt = provision_module(m, dry_run=True, plugin_root=plugins, staging_root=staging)
    reg_step = next(s for s in receipt["steps"] if s["kind"] == "plugin_register")
    assert reg_step["status"] == "would_register"
    assert not (tmp_path / "installed_plugins.json").exists()


def test_no_archive_no_register_step(tmp_path):
    m = _manifest(tmp_path)  # no plugin_archive → competence layer absent
    receipt = provision_module(m, dry_run=False, plugin_root=tmp_path / "plugins", staging_root=tmp_path / "s")
    assert not any(s["kind"] == "plugin_register" for s in receipt["steps"])


def test_register_plugin_preserves_existing_entries(tmp_path):
    registry = tmp_path / "installed_plugins.json"
    registry.write_text(
        json.dumps(
            {"version": 2, "plugins": {"empirica@local": [{"installPath": "/x", "version": "1.0", "isLocal": True}]}}
        )
    )
    staging = _staged_tar(tmp_path)
    m = _manifest(tmp_path, plugin_archive="outreach-0.4.0-plugin.tar.gz")
    provision_module(m, dry_run=False, plugin_root=tmp_path / "plugins", staging_root=staging)
    data = json.loads(registry.read_text())
    assert "empirica@local" in data["plugins"]  # preserved
    assert "outreach@local" in data["plugins"]  # added


# ── automation registration (loop register, mocked) ─────────────────────


def test_listener_maps_to_monitor_kind(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        executors.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0)
    )
    m = _manifest(
        tmp_path,
        automations=[{"name": "outreach-outbox-dispatcher", "kind": "listener", "command": "outreach dispatch-listen"}],
    )
    receipt = provision_module(m, dry_run=False, plugin_root=tmp_path / "p", staging_root=tmp_path / "s")
    auto_step = next(s for s in receipt["steps"] if s["kind"] == "automation")
    assert auto_step["status"] == "registered"
    assert calls[0][:5] == ["empirica", "loop", "register", "--name", "outreach-outbox-dispatcher"]
    assert "monitor" in calls[0]  # listener → monitor


def test_interval_automation_passes_interval(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        executors.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0)
    )
    m = _manifest(tmp_path, automations=[{"name": "poller", "kind": "interval", "interval": "5m"}])
    provision_module(m, dry_run=False, plugin_root=tmp_path / "p", staging_root=tmp_path / "s")
    # Assert the interval-registration call is AMONG the captured calls — provision
    # makes several subprocess calls (a git-notes message-store `for-each-ref` can
    # interleave), so it is not deterministically calls[0] under pytest-randomly.
    assert any("--interval" in c and "5m" in c for c in calls), f"no interval-registration call in {calls}"


def test_dry_run_registers_nothing(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(executors.subprocess, "run", lambda *a, **k: called.append(1))
    m = _manifest(tmp_path, automations=[{"name": "p", "kind": "interval", "interval": "5m"}])
    receipt = provision_module(m, dry_run=True, plugin_root=tmp_path / "p", staging_root=tmp_path / "s")
    auto_step = next(s for s in receipt["steps"] if s["kind"] == "automation")
    assert auto_step["status"] == "would_register" and not called


# ── topic grants (cortex admin, mocked) ─────────────────────────────────


def test_topics_unconfigured_without_org(tmp_path):
    m = _manifest(tmp_path, topics=["empirica-outreach-dispatch"])
    receipt = provision_module(
        m, dry_run=False, plugin_root=tmp_path / "p", staging_root=tmp_path / "s", cortex_url=None
    )
    topic_step = next(s for s in receipt["steps"] if s["kind"] == "topic")
    assert topic_step["status"] == "unconfigured"


def test_topics_dry_run_shows_grants(tmp_path):
    m = _manifest(tmp_path, topics=["empirica-outreach-dispatch"])
    receipt = provision_module(
        m,
        dry_run=True,
        plugin_root=tmp_path / "p",
        staging_root=tmp_path / "s",
        cortex_url="https://cortex.example",
        cortex_api_key="admin-key",
        org="empirica",
        tenant="david",
    )
    topic_step = next(s for s in receipt["steps"] if s["kind"] == "topic")
    assert topic_step["status"] == "would_grant"
    grants = json.loads(topic_step["detail"])
    assert {g["user"] for g in grants} == {"empirica-cortex-publisher", "empirica-u-david"}
    assert {g["permission"] for g in grants} == {"rw", "read-only"}


def test_topics_granted_calls_cortex(tmp_path, monkeypatch):
    posted = []
    monkeypatch.setattr(
        executors, "_post_grants", lambda url, key, grants: posted.append((url, grants)) or (True, "http 200")
    )
    m = _manifest(tmp_path, topics=["empirica-outreach-dispatch"])
    receipt = provision_module(
        m,
        dry_run=False,
        plugin_root=tmp_path / "p",
        staging_root=tmp_path / "s",
        cortex_url="https://cortex.example",
        cortex_api_key="admin-key",
        org="empirica",
        tenant="david",
    )
    topic_step = next(s for s in receipt["steps"] if s["kind"] == "topic")
    assert topic_step["status"] == "granted" and posted


# ── env presence ────────────────────────────────────────────────────────


def test_env_presence(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVTO_API_KEY", "x")
    monkeypatch.delenv("ZERNIO_API_KEY", raising=False)
    m = _manifest(tmp_path, env=["DEVTO_API_KEY", "ZERNIO_API_KEY"])
    receipt = provision_module(m, dry_run=False, plugin_root=tmp_path / "p", staging_root=tmp_path / "s")
    env_steps = {s["target"]: s["status"] for s in receipt["steps"] if s["kind"] == "env"}
    assert env_steps == {"DEVTO_API_KEY": "present", "ZERNIO_API_KEY": "missing"}
    assert receipt["ok"] is True  # missing env is reported, not an error


# ── CLI handler ─────────────────────────────────────────────────────────


def test_cli_provision_dry_run_exit_0(tmp_path, capsys):
    m_path = tmp_path / "module.yaml"
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
        path=str(m_path),
        dry_run=True,
        plugin_root=str(tmp_path / "p"),
        staging_root=str(tmp_path / "s"),
        cortex_url=None,
        org=None,
        tenant=None,
        output="json",
    )
    rc = handle_module_provision_command(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["action"] == "provision"


# ── provides.domains → practice_domains (A5) ─────────────────────────────


def _pd_steps(receipt):
    return [s for s in receipt["steps"] if s["kind"] == "practice_domain"]


def test_no_domains_no_practice_domain_steps(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(tmp_path / "workspace.db"))
    m = _manifest(tmp_path)
    receipt = provision_module(m, dry_run=False, plugin_root=tmp_path / "p", staging_root=tmp_path / "s")
    assert _pd_steps(receipt) == []


def test_domains_dry_run_registers_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(tmp_path / "workspace.db"))
    m = _manifest(tmp_path, domains=["outreach", "sales"])
    receipt = provision_module(m, dry_run=True, plugin_root=tmp_path / "p", staging_root=tmp_path / "s")
    pd = _pd_steps(receipt)
    assert {s["target"] for s in pd} == {"outreach", "sales"}
    assert all(s["status"] == "dry_run" for s in pd)


def test_domains_provisioned_write_practice_domains(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(tmp_path / "workspace.db"))
    m = _manifest(tmp_path, domains=["outreach", "support"])
    receipt = provision_module(m, dry_run=False, plugin_root=tmp_path / "p", staging_root=tmp_path / "s")
    pd = _pd_steps(receipt)
    assert {s["target"] for s in pd} == {"outreach", "support"}
    assert all(s["status"] == "ok" for s in pd)

    from empirica.data.repositories.workspace_db import WorkspaceDBRepository

    with WorkspaceDBRepository.open() as repo:
        joined = {d["domain_id"] for d in repo.get_practice_domains("empirica-outreach")}
    assert joined == {"outreach", "support"}


def test_unknown_domain_is_error_step(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(tmp_path / "workspace.db"))
    m = _manifest(tmp_path, domains=["not_a_domain"])
    receipt = provision_module(m, dry_run=False, plugin_root=tmp_path / "p", staging_root=tmp_path / "s")
    pd = _pd_steps(receipt)
    assert pd and pd[0]["status"] == "error"
    assert receipt["ok"] is False  # an error step makes the receipt not-ok

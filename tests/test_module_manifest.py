"""Tests for the practice-module manifest (``module.yaml``) schema + validator.

Covers: full valid load, the top-level ``empirica_module:`` requirement,
required-field + unknown-key (extra=forbid) rejection, the reference-only
``secrets_ref`` discipline, automation kind↔field consistency, the
``validate_manifest_file`` receipt shape, and the ``empirica module validate``
CLI exit codes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import yaml

from empirica.cli.command_handlers.module_commands import handle_module_validate_command
from empirica.core.modules.manifest import (
    ManifestError,
    load_manifest,
    validate_manifest_file,
)


def _valid_manifest() -> dict:
    return {
        "empirica_module": {
            "name": "outreach",
            "seat_name": "empirica-outreach",
            "version": "0.4.0",
            "visibility": "private",
            "requires": {"empirica_core": ">=1.11.6", "cortex_api": ">=v1"},
            "seat": {"import": "docs/OUTREACH_SEAT.md", "mode": "inject"},
            "artifacts": {
                "plugin_archive": "outreach-0.4.0-plugin.tar.gz",
                "python_packages": ["empirica-outreach==0.4.0"],
            },
            "provides": {
                "skills": ["devto", "voice-analyze", "brain-classifier"],
                "agents": ["outreach-search", "outreach-scout"],
                "automations": [
                    {
                        "name": "outreach-outbox-dispatcher",
                        "kind": "listener",
                        "command": "outreach dispatch-listen --instance empirica-outreach",
                        "autostart": True,
                        "restart_policy": "on-failure",
                    }
                ],
            },
            "requires_runtime": {
                "env": ["DEVTO_API_KEY", "ZERNIO_API_KEY"],
                "topics": ["empirica-outreach-dispatch"],
                "secrets_ref": "doppler://empirica/outreach",
            },
        }
    }


def _write(tmp_path, data: dict):
    p = tmp_path / "module.yaml"
    p.write_text(yaml.safe_dump(data))
    return p


# ── happy path ──────────────────────────────────────────────────────────


def test_valid_manifest_loads(tmp_path):
    m = load_manifest(_write(tmp_path, _valid_manifest()))
    assert m.name == "outreach"
    assert m.seat_name == "empirica-outreach"
    assert m.seat.import_ == "docs/OUTREACH_SEAT.md"  # alias 'import'
    assert m.seat.mode == "inject"
    assert m.provides.automations[0].kind == "listener"
    assert m.requires_runtime.secrets_ref == "doppler://empirica/outreach"


def test_round_trips_import_alias(tmp_path):
    receipt = validate_manifest_file(_write(tmp_path, _valid_manifest()))
    assert receipt["ok"] is True
    # by_alias dump re-emits 'import', not 'import_'
    assert receipt["manifest"]["seat"]["import"] == "docs/OUTREACH_SEAT.md"


def test_minimal_manifest_defaults(tmp_path):
    data = {
        "empirica_module": {
            "name": "x",
            "seat_name": "empirica-x",
            "version": "0.1.0",
            "seat": {"import": "docs/X_SEAT.md"},
        }
    }
    m = load_manifest(_write(tmp_path, data))
    assert m.visibility == "private"  # default
    assert m.seat.mode == "inject"  # default
    assert m.provides.skills == [] and m.requires_runtime.env == []


# ── structural rejection ────────────────────────────────────────────────


def test_missing_root_key(tmp_path):
    p = tmp_path / "module.yaml"
    p.write_text(yaml.safe_dump({"name": "x"}))
    with pytest.raises(ManifestError, match="empirica_module"):
        load_manifest(p)


def test_missing_required_field(tmp_path):
    data = _valid_manifest()
    del data["empirica_module"]["seat"]["import"]
    receipt = validate_manifest_file(_write(tmp_path, data))
    assert receipt["ok"] is False
    assert any("seat.import" in e for e in receipt["errors"])


def test_unknown_key_rejected(tmp_path):
    data = _valid_manifest()
    data["empirica_module"]["provides"]["skils"] = ["typo"]  # misspelled
    receipt = validate_manifest_file(_write(tmp_path, data))
    assert receipt["ok"] is False
    assert any("skils" in e or "extra" in e.lower() for e in receipt["errors"])


def test_missing_file(tmp_path):
    receipt = validate_manifest_file(tmp_path / "nope.yaml")
    assert receipt["ok"] is False
    assert any("not found" in e for e in receipt["errors"])


def test_invalid_yaml(tmp_path):
    p = tmp_path / "module.yaml"
    p.write_text("empirica_module: {name: x, : broken")
    receipt = validate_manifest_file(p)
    assert receipt["ok"] is False


# ── reference-only secrets discipline ───────────────────────────────────


@pytest.mark.parametrize(
    "ref",
    ["doppler://empirica/outreach", "op://vault/item", "vault://secret/data", "env:ZERNIO_API_KEY"],
)
def test_secrets_ref_accepts_references(tmp_path, ref):
    data = _valid_manifest()
    data["empirica_module"]["requires_runtime"]["secrets_ref"] = ref
    assert validate_manifest_file(_write(tmp_path, data))["ok"] is True


@pytest.mark.parametrize("raw", ["sk-abc123raw", "ZERNIO_KEY=topsecret", "just-a-string"])
def test_secrets_ref_rejects_raw_keys(tmp_path, raw):
    data = _valid_manifest()
    data["empirica_module"]["requires_runtime"]["secrets_ref"] = raw
    receipt = validate_manifest_file(_write(tmp_path, data))
    assert receipt["ok"] is False
    assert any("secrets_ref" in e for e in receipt["errors"])


# ── automation kind↔field consistency ───────────────────────────────────


def test_listener_requires_command(tmp_path):
    data = _valid_manifest()
    del data["empirica_module"]["provides"]["automations"][0]["command"]
    receipt = validate_manifest_file(_write(tmp_path, data))
    assert receipt["ok"] is False
    assert any("command" in e for e in receipt["errors"])


def test_interval_requires_interval(tmp_path):
    data = _valid_manifest()
    data["empirica_module"]["provides"]["automations"] = [
        {"name": "poller", "kind": "interval"}  # missing interval
    ]
    receipt = validate_manifest_file(_write(tmp_path, data))
    assert receipt["ok"] is False
    assert any("interval" in e for e in receipt["errors"])


def test_interval_automation_valid(tmp_path):
    data = _valid_manifest()
    data["empirica_module"]["provides"]["automations"] = [{"name": "poller", "kind": "interval", "interval": "5m"}]
    assert validate_manifest_file(_write(tmp_path, data))["ok"] is True


# ── CLI handler ─────────────────────────────────────────────────────────


def test_cli_validate_exit_0_on_valid(tmp_path, capsys):
    p = _write(tmp_path, _valid_manifest())
    rc = handle_module_validate_command(SimpleNamespace(path=str(p), output="json"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True


def test_cli_validate_exit_1_on_invalid(tmp_path, capsys):
    data = _valid_manifest()
    del data["empirica_module"]["version"]
    p = _write(tmp_path, data)
    rc = handle_module_validate_command(SimpleNamespace(path=str(p), output="json"))
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False


def test_cli_validate_text_output(tmp_path, capsys):
    p = _write(tmp_path, _valid_manifest())
    rc = handle_module_validate_command(SimpleNamespace(path=str(p), output="text"))
    text = capsys.readouterr().out
    assert rc == 0 and "outreach" in text and "valid" in text

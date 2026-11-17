"""provision-practice --from-protocol: Phase 6 from a workflow protocol file (goal 6e349146)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from empirica.cli.command_handlers import provision_practice_commands as pp

PROTOCOL = {
    "schema_version": "0.3.0",
    "user_profile": {"name": "Ada"},
    "mesh": {"cortex": True, "tenant": "ada", "org": "acme", "forgejo": {"owner": "ada", "host": "ssh://git@h:2222"}},
    "cockpit_profile": "ada",
    "practices": [
        {"name": "ada-coordinator", "type": "operations", "domain": "operations", "harness": "claude-code"},
        {
            "name": "acme-app",
            "type": "web-app",
            "harness": "both",
            "model": "claude-opus-5-5",
            "skills": ["empirica:pre-publish"],
        },
        {"name": "ada-notes", "harness": "none"},
        {"name": "ada-codex", "harness": "ecodex", "model": "gpt-x"},
    ],
}


def _args(tmp_path, protocol=PROTOCOL, **kw):
    path = tmp_path / "workflow-protocol.yaml"
    path.write_text(yaml.safe_dump(protocol))
    base = {
        "name": None, "from_protocol": str(path), "only": None, "base_path": str(tmp_path / "practices"), "type": None,
        "description": None, "domain": None, "tenant": None, "org": None, "substrate": None, "forgejo_owner": None,
        "forgejo_host": None, "no_cortex": False, "cockpit_profile": None, "launch": None, "resume": None,
        "second_pane": None, "no_local_server": False, "dry_run": True, "output": "json",
    }  # fmt: skip
    base.update(kw)
    return argparse.Namespace(**base)


def _run(tmp_path, capsys, **kw):
    seen = []

    def provision(ns):
        seen.append(ns)
        print(json.dumps({"ok": True, "name": ns.name, "steps": []}))
        return 0

    code = pp._provision_from_protocol(_args(tmp_path, **kw), provision=provision)
    return code, json.loads(capsys.readouterr().out), {ns.name: ns for ns in seen}


def test_each_practice_gets_its_own_fields_and_the_protocols_mesh(tmp_path, capsys):
    code, out, seen = _run(tmp_path, capsys)
    assert (
        code == 0 and out["ok"] and [p["name"] for p in out["practices"]] == [p["name"] for p in PROTOCOL["practices"]]
    )
    coord = seen["ada-coordinator"]
    assert (coord.type, coord.domain, coord.tenant, coord.org) == ("operations", "operations", "ada", "acme")
    assert (coord.forgejo_owner, coord.forgejo_host, coord.cockpit_profile) == ("ada", "ssh://git@h:2222", "ada")
    assert coord.launch is None and coord.second_pane is None  # claude, the default pane
    app = seen["acme-app"]
    assert (app.launch, app.second_pane) == ("claude --model claude-opus-5-5", "ecodex")
    assert seen["ada-codex"].launch == "ecodex --model gpt-x"
    assert seen["ada-notes"].cockpit_profile is None and seen["ada-notes"].launch is None  # desktop or web only


def test_skills_are_listed_never_installed(tmp_path, capsys):
    _, out, _ = _run(tmp_path, capsys)
    app = next(p for p in out["practices"] if p["name"] == "acme-app")
    assert app["suggested_skills"] == ["empirica:pre-publish"]


def test_flags_given_here_win_over_the_protocol(tmp_path, capsys):
    _, _, seen = _run(tmp_path, capsys, org="other", cockpit_profile="mine")
    assert seen["ada-coordinator"].org == "other" and seen["ada-coordinator"].cockpit_profile == "mine"


def test_only_selects_and_an_unknown_name_is_refused(tmp_path, capsys):
    _, out, seen = _run(tmp_path, capsys, only=["acme-app"])
    assert list(seen) == ["acme-app"]
    code, out, seen = _run(tmp_path, capsys, only=["nope"])
    assert code == 1 and not seen and "nope" in out["error"]


def test_an_invalid_protocol_provisions_nothing(tmp_path, capsys):
    bad = {**PROTOCOL, "practices": [{"name": "x", "harness": "cursor"}]}
    code, out, seen = _run(tmp_path, capsys, protocol=bad)
    assert code == 1 and not seen and out["problems"]


def test_name_and_protocol_together_are_refused(tmp_path, capsys):
    code, _out, seen = _run(tmp_path, capsys, name="x")
    assert code == 1 and not seen


def test_a_shared_repo_is_cloned_when_missing_and_left_when_present(tmp_path):
    calls = []

    def run(cmd, **_kw):
        calls.append(cmd)
        Path(cmd[-1]).mkdir(parents=True)
        return argparse.Namespace(returncode=0, stdout="", stderr="")

    practice = {"name": "shared", "repository": "ssh://git@h:2222/acme/shared.git"}
    target = tmp_path / "shared"
    assert pp._clone_step(practice, target, dry_run=False, run=run) == {"step": "clone", "changed": True}
    assert calls == [["git", "clone", "--", practice["repository"], str(target)]]
    assert pp._clone_step(practice, target, dry_run=False, run=run)["changed"] is False
    assert pp._clone_step({"name": "x"}, target, dry_run=False, run=run) is None


def test_a_cloned_shared_repo_keeps_the_persons_tenant_out_of_the_tracked_file(tmp_path):
    root = tmp_path / "shared"
    (root / ".empirica").mkdir(parents=True)
    tracked = root / ".empirica" / "project.yaml"
    tracked.write_text(
        yaml.safe_dump({"ai_id": "shared", "org_id": "acme", "seats": "per-clone", "substrate": "cortex"})
    )
    pp._patch_project_yaml(tracked, ai_id="shared", tenant="ada", org="acme", substrate="cortex", dry_run=False)
    assert "tenant_slug" not in yaml.safe_load(tracked.read_text())
    assert yaml.safe_load((root / ".empirica" / "project.local.yaml").read_text())["tenant_slug"] == "ada"

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


def test_each_practice_gets_its_own_fields_and_never_the_protocols_infrastructure(tmp_path, capsys):
    """A stored profile is person-written: tenant, org, Forgejo and the cockpit profile come from this command's flags or
    the sign-in, never from the file (cortex, prop_npmgug5zcja6pgvpco5vaiqunm)."""
    code, out, seen = _run(tmp_path, capsys, cockpit_profile="ada")
    assert (
        code == 0 and out["ok"] and [p["name"] for p in out["practices"]] == [p["name"] for p in PROTOCOL["practices"]]
    )
    coord = seen["ada-coordinator"]
    assert (coord.type, coord.domain) == ("operations", "operations")
    assert (coord.tenant, coord.org, coord.forgejo_owner, coord.forgejo_host) == (None, None, None, None)
    assert coord.launch is None and coord.second_pane is None  # claude, the default pane
    assert set(out["ignored_from_protocol"]) == {"mesh", "cockpit_profile"}
    app = seen["acme-app"]
    assert (app.launch, app.second_pane) == ("claude --model claude-opus-5-5", "ecodex")
    assert seen["ada-codex"].launch == "ecodex --model gpt-x"
    assert seen["ada-notes"].cockpit_profile is None and seen["ada-notes"].launch is None  # desktop or web only


def test_without_a_cockpit_flag_no_pane_is_added(tmp_path, capsys):
    _, _, seen = _run(tmp_path, capsys)
    assert all(ns.cockpit_profile is None and ns.launch is None for ns in seen.values())


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


def test_a_refusal_on_stderr_and_an_exception_are_that_practices_error(tmp_path, capsys):
    import sys

    def provision(ns):
        if ns.name == "acme-app":
            raise PermissionError("denied")
        print(json.dumps({"ok": False, "error": "--tenant/--org not given"}), file=sys.stderr)
        return 1

    code = pp._provision_from_protocol(_args(tmp_path), provision=provision)
    out = json.loads(capsys.readouterr().out)
    errors = {p["name"]: p.get("error") for p in out["practices"]}
    assert code == 1 and len(errors) == len(PROTOCOL["practices"])  # every practice reported
    assert errors["acme-app"] == "PermissionError: denied" and errors["ada-coordinator"] == "--tenant/--org not given"


def test_a_protocol_without_practices_is_valid_and_does_nothing(tmp_path, capsys):
    code = pp._provision_from_protocol(
        _args(tmp_path, protocol={"schema_version": "0.3.0", "user_profile": {"name": "A"}})
    )
    assert code == 0 and json.loads(capsys.readouterr().out)["practices"] == []


def test_a_clone_without_git_or_into_a_foreign_folder_is_an_error(tmp_path):
    practice = {"name": "shared", "repository": "ssh://git@h:2222/acme/shared.git"}

    def no_git(*_a, **_kw):
        raise FileNotFoundError("git")

    assert "FileNotFoundError" in pp._clone_step(practice, tmp_path / "shared", dry_run=False, run=no_git)["error"]
    (tmp_path / "other").mkdir()
    assert "not a git clone" in pp._clone_step(practice, tmp_path / "other", dry_run=False, run=no_git)["error"]


def test_a_shared_repos_tracked_identity_is_never_rewritten(tmp_path):
    root = tmp_path / "shared"
    (root / ".empirica").mkdir(parents=True)
    tracked = root / ".empirica" / "project.yaml"
    original = {"ai_id": "research", "org_id": "teamorg", "seats": "per-clone", "substrate": "cortex"}
    tracked.write_text(yaml.safe_dump(original))
    pp._patch_project_yaml(tracked, ai_id="team-research", tenant="ada", org="acme", substrate="local", dry_run=False)
    assert yaml.safe_load(tracked.read_text()) == original


def test_what_a_practice_prints_to_stderr_is_still_shown(tmp_path, capsys):
    import sys

    def provision(ns):
        print("  [dry-run] would run: project-init", file=sys.stderr)
        print(json.dumps({"ok": True, "name": ns.name, "steps": []}))
        return 0

    pp._provision_from_protocol(_args(tmp_path), provision=provision)
    assert "[dry-run] would run: project-init" in capsys.readouterr().err


def test_a_shared_repo_dry_run_says_where_the_seat_goes(tmp_path, capsys):
    root = tmp_path / "shared"
    (root / ".empirica").mkdir(parents=True)
    tracked = root / ".empirica" / "project.yaml"
    tracked.write_text(yaml.safe_dump({"ai_id": "shared", "seats": "per-clone"}))
    assert pp._patch_project_yaml(tracked, ai_id="shared", tenant="ada", org="o", substrate="cortex", dry_run=True)
    assert "project.local.yaml" in capsys.readouterr().err
    (root / ".empirica" / "project.local.yaml").write_text(yaml.safe_dump({"tenant_slug": "ada"}))
    assert not pp._patch_project_yaml(tracked, ai_id="shared", tenant="ada", org="o", substrate="cortex", dry_run=True)

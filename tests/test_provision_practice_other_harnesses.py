"""Onboarding a practice that does not run Claude (ecodex prop_b2ejujuvfbg4jo5y356slzxbey, David's rulings 2026-10-24).

ecodex's interview provisions practices through core's own `provision-practice --cockpit-profile` path. Four things stopped it:

* `--dry-run --output json` printed `[dry-run] would run:` lines to stdout before the JSON, so stdout did not parse. There were two such
  lines of narration, one in `_run` and one in `_patch_project_yaml`; ecodex cited only the first. Narration belongs on stderr.
* the cockpit profile hardcoded `launch: claude` for every practice. `--launch` / `--resume` now say what the pane runs.
* `cockpit refresh` revived anything that was not claude by re-running its launch line, so a codex/ecodex pane came back as a NEW
  session. It resumes with `<program> resume --last`.
* `--no-cortex` demanded `--tenant`/`--org`. A first-time standalone user has neither, and a local practice has no tenant to name.

Everything is built under tmp_path with HOME pinned; `_run` is stubbed where a real `empirica` subprocess would run.
"""

from __future__ import annotations

import json
import types

import pytest

yaml = pytest.importorskip("yaml")

from empirica.cli.command_handlers import provision_practice_commands as pp  # noqa: E402
from empirica.core.cockpit.launcher.config import ProjectSpec, add_practice_to_profile, load_config  # noqa: E402
from empirica.core.cockpit.launcher.tmux import resume_command  # noqa: E402


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    elsewhere = tmp_path / "elsewhere"  # a cwd with no project.yaml, so nothing is inferred
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    return h


def _args(tmp_path, **kw):
    base = {
        "name": "p",
        "base_path": str(tmp_path / "base"),
        "tenant": "t",
        "org": "o",
        "no_cortex": False,
        "substrate": None,
        "cockpit_profile": None,
        "launch": None,
        "resume": None,
        "forgejo_owner": None,
        "forgejo_host": None,
        "dry_run": False,
        "output": "json",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _practice(tmp_path, **fields):
    d = tmp_path / "base" / "p" / ".empirica"
    d.mkdir(parents=True)
    (d / "project.yaml").write_text(yaml.safe_dump(fields))
    return d / "project.yaml"


@pytest.fixture
def stub_run(monkeypatch):
    calls = []
    monkeypatch.setattr(pp, "_run", lambda cmd, cwd, dry_run: (calls.append(cmd), (True, ""))[1])
    return calls


# ── 3. dry-run narration is stderr, so --output json parses ─────────────────


def test_dry_run_json_stdout_is_exactly_the_json(tmp_path, capsys):
    rc = pp.handle_provision_practice_command(_args(tmp_path, dry_run=True))
    out, err = capsys.readouterr()
    assert rc == 0
    payload = json.loads(out)  # raises if narration is mixed in
    assert payload["ok"] is True and payload["dry_run"] is True
    assert "[dry-run]" not in out
    assert "[dry-run] would run:" in err, "the narration still exists; it moved, it was not deleted"


def test_the_yaml_patch_narration_is_stderr_too(tmp_path, capsys):
    """The second polluter ecodex did not cite: an existing project.yaml that needs a change prints a patch line."""
    _practice(tmp_path, name="p")
    rc = pp.handle_provision_practice_command(_args(tmp_path, dry_run=True))
    out, err = capsys.readouterr()
    assert rc == 0
    json.loads(out)
    assert "[dry-run] would patch" in err and "[dry-run]" not in out


def test_human_dry_run_keeps_its_summary_on_stdout_and_the_narration_on_stderr(tmp_path, capsys):
    pp.handle_provision_practice_command(_args(tmp_path, dry_run=True, output="human"))
    out, err = capsys.readouterr()
    assert "[dry-run] would run:" in err and "[dry-run] would run:" not in out
    assert out.strip(), "the human summary is still printed"


# ── the --no-cortex question: a local practice names no tenant ───────────────


def test_no_cortex_needs_no_tenant_or_org(tmp_path, capsys):
    rc = pp.handle_provision_practice_command(_args(tmp_path, tenant=None, org=None, no_cortex=True, dry_run=True))
    out, _ = capsys.readouterr()
    payload = json.loads(out)
    assert rc == 0 and payload["ok"] is True
    assert payload["substrate"] == "local" and payload["tenant"] is None and payload["org"] is None


def test_a_cortex_practice_still_needs_them(tmp_path, capsys):
    rc = pp.handle_provision_practice_command(_args(tmp_path, tenant=None, org=None, dry_run=True))
    _, err = capsys.readouterr()
    assert rc == 1 and "--tenant/--org" in err


def test_a_tenant_given_to_a_local_practice_is_still_written(tmp_path, stub_run, capsys):
    path = _practice(tmp_path, name="p")
    pp.handle_provision_practice_command(_args(tmp_path, no_cortex=True))
    capsys.readouterr()
    data = yaml.safe_load(path.read_text())
    assert data["tenant_slug"] == "t" and data["org_id"] == "o" and data["substrate"] == "local"


def test_a_local_practice_without_tenant_writes_no_empty_tenant_keys(tmp_path, stub_run, capsys):
    """None must not be written down as a tenant: a null tenant_slug reads as 'present' to code that checks the key."""
    path = _practice(tmp_path, name="p")
    pp.handle_provision_practice_command(_args(tmp_path, tenant=None, org=None, no_cortex=True))
    capsys.readouterr()
    data = yaml.safe_load(path.read_text())
    assert data["ai_id"] == "p" and data["substrate"] == "local"
    assert "tenant_slug" not in data and "org_id" not in data


# ── 1. --launch / --resume ───────────────────────────────────────────────────


def test_the_profile_records_the_launch_and_resume_it_was_given(tmp_path):
    f, _ = add_practice_to_profile(
        "me", "ecodex-lab", str(tmp_path / "lab"), launch="ecodex", resume="ecodex resume --last"
    )
    spec = load_config(f).project_by_name("ecodex-lab")
    assert spec.launch == "ecodex" and spec.resume == "ecodex resume --last"


def test_without_flags_the_profile_still_launches_claude(tmp_path):
    f, _ = add_practice_to_profile("me", "plain", str(tmp_path / "plain"))
    spec = load_config(f).project_by_name("plain")
    assert spec.launch == "claude" and not spec.resume


def test_provision_passes_launch_and_resume_to_the_profile(tmp_path, stub_run, capsys):
    _practice(tmp_path, name="p")
    rc = pp.handle_provision_practice_command(
        _args(tmp_path, cockpit_profile="me", launch="ecodex", resume="ecodex resume --last", no_cortex=True)
    )
    capsys.readouterr()
    assert rc == 0
    spec = load_config(pp_profile_path("me")).project_by_name("p")
    assert spec.launch == "ecodex" and spec.resume == "ecodex resume --last"


def pp_profile_path(name):
    from empirica.core.cockpit.launcher.config import profile_path

    return profile_path(name)


@pytest.mark.parametrize("flag", [{"launch": "ecodex"}, {"resume": "ecodex resume --last"}])
def test_launch_or_resume_without_a_profile_is_refused_not_ignored(tmp_path, capsys, flag):
    """An advertised flag that does nothing is worse than a missing one: it looks like it worked."""
    rc = pp.handle_provision_practice_command(_args(tmp_path, dry_run=True, **flag))
    _, err = capsys.readouterr()
    assert rc == 1 and "--cockpit-profile" in err


# ── 2. cockpit refresh resumes codex / ecodex ────────────────────────────────


def _proj(launch, resume=None):
    return ProjectSpec(name="x", path="/tmp/x", launch=launch, resume=resume)


@pytest.mark.parametrize(
    ("launch", "expected"),
    [
        ("codex", "codex resume --last"),
        ("ecodex", "ecodex resume --last"),
        ("/usr/local/bin/ecodex", "/usr/local/bin/ecodex resume --last"),
        ("codex -m gpt-x", "codex -m gpt-x resume --last"),
    ],
)
def test_a_codex_family_pane_resumes_its_last_session(launch, expected):
    assert resume_command(_proj(launch)) == expected


def test_a_launch_that_already_resumes_is_left_alone():
    assert resume_command(_proj("codex resume --last")) == "codex resume --last"


def test_an_explicit_resume_still_wins():
    assert resume_command(_proj("codex", resume="codex resume abc123")) == "codex resume abc123"


@pytest.mark.parametrize("launch", ["bash", "htop", "python3 serve.py"])
def test_anything_else_that_is_not_claude_is_still_re_run_as_launched(launch):
    assert resume_command(_proj(launch)) == launch

"""`cockpit status` validates the profile instead of echoing it back.

ecodex (prop_yobqvoahera4nkm7cehxojfcim, measured): a profile whose group pane names
`{project: no-such-practice}` came back ok:true from `cockpit status --config <file> --output json`,
and the mistake only showed at launch, as a bash placeholder pane. Status is the no-side-effect
check of a profile; it has to fail on what launch cannot honour.

Second gap, same request: `provision-practice` puts a practice under ~/empirica by default, and
first-launch discovery only looked under ~/empirical-ai, so a default-provisioned practice never
appeared in an auto-generated cockpit.

Everything is built under tmp_path with HOME pinned.
"""

from __future__ import annotations

import json
import types

import pytest

from empirica.cli.command_handlers import cockpit_launcher_commands as cmds
from empirica.core.cockpit.launcher import config as cfg


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _status(path, capsys):
    rc = cmds.handle_cockpit_status_command(types.SimpleNamespace(config=str(path), profile=None, output="json"))
    return rc, json.loads(capsys.readouterr().out)


def _write(path, text):
    path.write_text(text)
    return path


GOOD = """
session_name: t
projects:
  - {name: alpha, path: %s, launch: claude}
groups:
  - name: g
    panes:
      - {project: alpha}
"""


def test_a_group_pane_naming_an_unlisted_project_fails_status(tmp_path, capsys):
    """The reported case, verbatim shape."""
    f = _write(
        tmp_path / "c.yaml",
        "session_name: t\nprojects: []\ngroups:\n  - name: g\n    panes:\n      - {project: no-such-practice}\n",
    )

    rc, out = _status(f, capsys)

    assert rc == 1 and out["ok"] is False
    assert any("no-such-practice" in p and "not listed under projects" in p for p in out["problems"])


def test_a_valid_profile_still_passes_status(tmp_path, capsys):
    """CONTROL: the validator must not fail everything."""
    d = tmp_path / "alpha"
    d.mkdir()
    f = _write(tmp_path / "c.yaml", GOOD % d)

    rc, out = _status(f, capsys)

    assert rc == 0 and out["ok"] is True and out["problems"] == [] and out["config_file"] == "ok"


def test_a_missing_project_directory_is_a_warning_not_a_failure(tmp_path, capsys):
    """Launch leaves a placeholder pane for it, so status says so without failing."""
    f = _write(tmp_path / "c.yaml", GOOD % (tmp_path / "not-yet"))

    rc, out = _status(f, capsys)

    assert rc == 0 and out["ok"] is True
    assert any("does not exist yet" in w for w in out["warnings"])


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("groups:\n  - name: g\n    panes: []\n", "has no usable pane"),
        (
            "groups:\n  - {name: g, panes: [{command: a}]}\n  - {name: g, panes: [{command: b}]}\n",
            "appears more than once",
        ),
        ("groups:\n  - name: g\n    panes:\n      - {label: x}\n", "names neither a project nor a command"),
        (
            "projects:\n  - {name: a, path: /x}\n  - {name: a, path: /y}\ngroups:\n  - {name: g, panes: [{command: a}]}\n",
            "listed more than once",
        ),
    ],
)
def test_other_launch_hostile_shapes_are_errors(tmp_path, capsys, body, needle):
    f = _write(tmp_path / "c.yaml", "session_name: t\n" + body)

    rc, out = _status(f, capsys)

    assert rc == 1 and any(needle in p for p in out["problems"]), out["problems"]


def test_a_config_file_that_cannot_be_read_is_not_reported_as_its_defaults(tmp_path, capsys):
    """load_config answers a YAML typo with the built-in layout; status used to print that as the profile."""
    f = _write(tmp_path / "c.yaml", "session_name: [unclosed\n")

    rc, out = _status(f, capsys)

    assert rc == 1 and out["config_file"] == "unreadable"
    assert "unreadable" in out["problems"][0] and "built-in defaults" in out["problems"][0]


def test_an_explicit_config_that_does_not_exist_is_a_problem(tmp_path, capsys):
    rc, out = _status(tmp_path / "nope.yaml", capsys)

    assert rc == 1 and out["config_file"] == "missing"


def test_the_human_report_prints_the_problem(tmp_path, capsys):
    f = _write(tmp_path / "c.yaml", "groups:\n  - name: g\n    panes:\n      - {project: ghost}\n")

    rc = cmds.handle_cockpit_status_command(types.SimpleNamespace(config=str(f), profile=None, output="human"))

    assert rc == 1 and "ghost" in capsys.readouterr().out


# ── discovery roots ──────────────────────────────────────────────────────────


def _practice(root, name):
    (root / name / ".empirica").mkdir(parents=True)


def test_first_launch_finds_a_practice_provisioned_under_the_default_base_path(home):
    """provision-practice's default is ~/empirica/<name>; discovery used to look only in ~/empirical-ai."""
    _practice(home / "empirica", "fresh-practice")

    assert [p.name for p in cfg.detect_projects()] == ["fresh-practice"]


def test_both_default_roots_are_searched_and_a_shared_name_is_listed_once(home):
    _practice(home / "empirical-ai", "core")
    _practice(home / "empirical-ai", "shared")
    _practice(home / "empirica", "shared")
    _practice(home / "empirica", "extra")

    found = cfg.detect_projects()

    assert [p.name for p in found] == ["core", "shared", "extra"]
    assert found[1].path == str((home / "empirical-ai" / "shared").resolve()), "the first root wins"


def test_an_explicit_root_searches_only_that_root(home, tmp_path):
    """CONTROL: the existing contract (a caller naming a root is not widened)."""
    _practice(home / "empirica", "elsewhere")
    root = tmp_path / "only"
    _practice(root, "mine")

    assert [p.name for p in cfg.detect_projects(projects_root=root)] == ["mine"]


def test_entries_the_loader_drops_are_reported_not_silently_missing(tmp_path, capsys):
    """A typo in one pane made its window vanish, and status said ok: the loader skips what it cannot
    parse, so a check on the loaded config never saw it."""
    d = tmp_path / "alpha"
    d.mkdir()
    f = _write(
        tmp_path / "c.yaml",
        f"""
projects:
  - {{name: alpha, path: {d}}}
  - {{name: nopath}}
  - just-a-string
groups:
  - name: ok
    panes: [{{project: alpha}}]
  - panes: [{{command: x}}]
  - name: half
    panes: [{{project: alpha}}, {{label: orphan}}, 7]
""",
    )

    rc, out = _status(f, capsys)
    joined = " | ".join(out["problems"])

    assert rc == 1
    for needle in (
        "projects[2] is ignored",
        "projects[3] is ignored",
        "groups[2] is ignored: has no name",
        "group 'half' pane 2 is ignored",
        "group 'half' pane 3 is ignored",
    ):
        assert needle in joined, (needle, out["problems"])
    assert "group 'ok'" not in joined, "CONTROL: a clean group is not reported"


def test_a_profile_the_writer_produced_validates_clean(tmp_path):
    """The validator and the writer must agree: what provision-practice writes is never an error."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    cfg.add_practice_to_profile("me", "a", str(tmp_path / "a"))
    f, _ = cfg.add_practice_to_profile("me", "b", str(tmp_path / "b"))

    errors, warnings = cfg.validate_file(f)

    assert errors == [] and warnings == []


# ── hardening from the 1.14.5 broccoli sweep ────────────────────────────────


def _default_config(home, text):
    d = home / ".empirica" / "cockpit"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.yaml").write_text(text)


def _status_default(capsys):
    rc = cmds.handle_cockpit_status_command(types.SimpleNamespace(config=None, profile=None, output="json"))
    return rc, json.loads(capsys.readouterr().out)


def test_a_broken_default_config_is_not_reported_healthy(home, capsys, monkeypatch):
    """Only an explicit --config was checked; the default file, the common path, reported ok:true."""
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", home / ".empirica" / "cockpit" / "config.yaml")
    _default_config(home, "projects: [\n  - name: a\n")

    rc, out = _status_default(capsys)

    assert rc == 1 and out["config_file"] == "unreadable" and "unreadable" in out["problems"][0]


def test_a_missing_default_config_is_normal_first_launch_writes_it(home, capsys, monkeypatch):
    """CONTROL: only unreadable and not-a-mapping are errors on the default path."""
    monkeypatch.setattr(cfg, "DEFAULT_CONFIG_PATH", home / ".empirica" / "cockpit" / "config.yaml")

    rc, out = _status_default(capsys)

    assert rc == 0 and out["config_file"] == "missing" and out["problems"] == []


@pytest.mark.parametrize("body", ["projects: 5\n", "groups: 5\n", "status_windows: x\n", "alacritty_args: 5\n"])
def test_a_section_of_the_wrong_type_is_a_finding_not_a_traceback(tmp_path, capsys, body):
    """`projects: 5` raised TypeError out of load_config, and so out of `status`."""
    f = _write(tmp_path / "c.yaml", "session_name: t\n" + body)

    rc, out = _status(f, capsys)

    assert rc == 1 and any("expected a list" in p for p in out["problems"]), out["problems"]


def test_panes_of_the_wrong_type_are_reported(tmp_path, capsys):
    f = _write(tmp_path / "c.yaml", "groups:\n  - name: g\n    panes: 5\n")

    rc, out = _status(f, capsys)

    assert rc == 1 and any("panes is ignored" in p for p in out["problems"])


def test_a_symlink_alias_of_a_practice_is_listed_once(home):
    _practice(home / "empirical-ai", "foo")
    (home / "empirica").mkdir()
    (home / "empirica" / "alias").symlink_to(home / "empirical-ai" / "foo")

    found = cfg.detect_projects()

    assert [p.name for p in found] == ["foo"], "two names for one directory is two panes on one project"


def test_an_unreadable_root_finds_nothing_instead_of_raising(home):
    _practice(home / "empirical-ai", "core")
    (home / "empirica").mkdir()
    (home / "empirica").chmod(0)
    try:
        if (home / "empirica").exists() and __import__("os").access(home / "empirica", __import__("os").R_OK):
            pytest.skip("running as a user that ignores directory permissions")
        assert [p.name for p in cfg.detect_projects()] == ["core"]
    finally:
        (home / "empirica").chmod(0o700)


def test_a_group_name_with_a_dot_is_flagged_because_tmux_cannot_address_it(tmp_path, capsys):
    """tmux reads '.' in a window target as a pane separator ("can't find window: my")."""
    f = _write(tmp_path / "c.yaml", "groups:\n  - {name: my.proj, panes: [{command: x}]}\n")

    rc, out = _status(f, capsys)

    assert rc == 1 and any("pane separator" in p for p in out["problems"])


def test_the_profile_writer_gives_a_dotted_practice_a_usable_window_name(tmp_path):
    """The group name is arbitrary; the project name (which the pane runs) stays exact."""
    f, _ = cfg.add_practice_to_profile("me", "my.proj", str(tmp_path / "my.proj"))
    config = cfg.load_config(f)

    assert [g.name for g in config.groups] == ["monitor", "my-proj"]
    assert config.project_by_name("my.proj") is not None and config.groups[1].panes[0].project_ref == "my.proj"
    assert cfg.validate_file(f)[0] == []

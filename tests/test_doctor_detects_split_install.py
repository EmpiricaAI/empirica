"""A box running several copies of core must be told so.

Incident 2026-09-17. A change shipped, and within minutes four practices measured
it as broken — "accepts the keys, returns ok, stores NULL" — and half the mesh woke
on it. The code was correct and had never executed. After a release, one box was
running THREE cores at once: the source tree at HEAD, a non-editable pipx CLI one
version back, and a deployed plugin also one version back.

Every obvious check lies about this. `python3 -c "import empirica"` shows the
working tree. `pip show` shows the editable install. Only the interpreter on the
BINARY'S OWN SHEBANG tells the truth — and the first diagnosis of the incident
tested the wrong interpreter and wrongly ruled staleness out.

These tests build their binary, interpreter answers and plugin manifest under
`tmp_path`. A test that read this box's real `empirica` would pass or fail on the
state of whoever ran it, which is how two earlier tests in this repo passed locally
for exactly the reason they could never pass in CI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from empirica.cli.command_handlers import diagnose as D


def _fake_binary(tmp_path: Path, shebang: str) -> str:
    b = tmp_path / "empirica"
    b.write_text(f"{shebang}\nimport sys\n")
    return str(b)


def _plugin(tmp_path: Path, version: str) -> Path:
    claude = tmp_path / ".claude"
    m = claude / "plugins" / "local" / "empirica" / ".claude-plugin"
    m.mkdir(parents=True)
    (m / "plugin.json").write_text(json.dumps({"version": version}))
    return claude


@pytest.fixture
def here():
    """What THIS process imports — the side of the comparison we do not fake."""
    import empirica

    return {
        "path": str(Path(empirica.__file__).resolve().parent),
        "version": getattr(empirica, "__version__", "?"),
    }


def _wire(monkeypatch, binary_path: str, answer: dict | None):
    monkeypatch.setattr(D.shutil, "which", lambda name: binary_path if name == "empirica" else None)
    monkeypatch.setattr(D, "_probe_interpreter", lambda _python: answer)


# ─── shebang parsing ──────────────────────────────────────────────────


def test_a_direct_shebang_names_its_interpreter(tmp_path):
    b = _fake_binary(tmp_path, "#!/opt/venv/bin/python")

    assert D._binary_interpreter(b) == "/opt/venv/bin/python"


def test_an_env_shebang_is_resolved_not_returned_as_env(tmp_path, monkeypatch):
    """`#!/usr/bin/env python3` names env. Probing `env` would import nothing and
    the check would report on the wrong program entirely."""
    b = _fake_binary(tmp_path, "#!/usr/bin/env python3")
    monkeypatch.setattr(D.shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)

    assert D._binary_interpreter(b) == "/usr/bin/python3"


def test_a_file_with_no_shebang_yields_none(tmp_path):
    b = tmp_path / "empirica"
    b.write_text("not a script\n")

    assert D._binary_interpreter(str(b)) is None


# ─── the check ────────────────────────────────────────────────────────


def test_the_incident_is_detected(tmp_path, monkeypatch, here):
    """The defect, as an assertion: the binary imports a DIFFERENT copy."""
    b = _fake_binary(tmp_path, "#!/opt/pipx/bin/python")
    _wire(monkeypatch, b, {"path": "/opt/pipx/lib/site-packages/empirica", "version": "1.13.46"})

    r = D.check_single_install(_plugin(tmp_path, here["version"]))

    assert r.status == D.WARN
    assert "SPLIT INSTALL" in r.detail
    assert "/opt/pipx/lib/site-packages/empirica" in r.detail, "name BOTH paths — a bare 'mismatch' cannot be acted on"
    assert here["path"] in r.detail
    assert "editable" in r.hint


def test_a_copy_at_the_RIGHT_version_is_still_a_split(tmp_path, monkeypatch, here):
    """The discriminator is the path, not the version string.

    A non-editable copy at the current version passes any version comparison and
    goes stale at the very next commit — which is exactly what made 1.13.46
    non-editable invisible to whatever check existed.
    """
    b = _fake_binary(tmp_path, "#!/opt/pipx/bin/python")
    _wire(monkeypatch, b, {"path": "/somewhere/else/empirica", "version": here["version"]})

    r = D.check_single_install(_plugin(tmp_path, here["version"]))

    assert r.status == D.WARN


def test_a_stale_plugin_is_reported_separately(tmp_path, monkeypatch, here):
    """The half of the incident that was still live after the CLI was fixed."""
    b = _fake_binary(tmp_path, "#!/opt/venv/bin/python")
    _wire(monkeypatch, b, dict(here))

    r = D.check_single_install(_plugin(tmp_path, "0.0.1"))

    assert r.status == D.WARN
    assert "deployed plugin is v0.0.1" in r.detail


def test_an_aligned_box_passes(tmp_path, monkeypatch, here):
    """Positive control.

    Without it, a check that always WARNed would satisfy everything above while
    teaching every healthy box to ignore it.
    """
    b = _fake_binary(tmp_path, "#!/opt/venv/bin/python")
    _wire(monkeypatch, b, dict(here))

    r = D.check_single_install(_plugin(tmp_path, here["version"]))

    assert r.status == D.PASS
    assert r.hint == ""


def test_no_binary_skips_rather_than_passing(tmp_path, monkeypatch):
    """ "Nothing to compare" is not "everything agrees"."""
    monkeypatch.setattr(D.shutil, "which", lambda _n: None)

    assert D.check_single_install(tmp_path).status == D.SKIP


def test_an_unanswering_interpreter_is_a_warn_not_a_pass(tmp_path, monkeypatch):
    """If the binary's interpreter cannot even import empirica, that is a finding.

    PASS here would be a verdict over nothing; SKIP would hide that the binary is
    very likely broken.
    """
    b = _fake_binary(tmp_path, "#!/opt/venv/bin/python")
    _wire(monkeypatch, b, None)

    assert D.check_single_install(tmp_path).status == D.WARN


def test_a_missing_plugin_does_not_fail_this_check(tmp_path, monkeypatch, here):
    """An absent plugin is `check_plugin_files`' business, not this check's.

    Reporting it twice would make one problem look like two.
    """
    b = _fake_binary(tmp_path, "#!/opt/venv/bin/python")
    _wire(monkeypatch, b, dict(here))

    r = D.check_single_install(tmp_path / "no-claude-dir")

    assert r.status == D.PASS
    assert r.data["plugin_version"] is None


def test_the_check_is_wired_into_the_run():
    """A check nobody calls is its own version of the defect."""
    import inspect

    assert "check_single_install(" in inspect.getsource(D).split("def run_", 1)[-1] or (
        "results.append(check_single_install(" in inspect.getsource(D)
    )


# ─── the probe must not be fooled by its own working directory ────────


def test_the_probe_ignores_a_package_in_the_callers_cwd(tmp_path, monkeypatch):
    """`python -c` puts `''` on sys.path, so cwd can masquerade as the install.

    A probe launched from inside the repo imports `./empirica` whatever is
    installed, reports the working tree, and CLEARS a split box. That is how the
    incident's first diagnosis ruled staleness out — and the first draft of
    `_probe_interpreter` inherited the caller's cwd and would have repeated it.

    Uses a REAL subprocess against a decoy package, because the defect lives in
    how the child resolves imports and no monkeypatch of the parent can show it.
    """
    import sys

    decoy = tmp_path / "empirica"
    decoy.mkdir()
    (decoy / "__init__.py").write_text('__version__ = "0.0.0-decoy"\n')
    monkeypatch.chdir(tmp_path)

    answer = D._probe_interpreter(sys.executable)

    assert answer is not None, "the real interpreter can import the real package"
    assert answer["version"] != "0.0.0-decoy", "the probe imported a package out of the caller's cwd"
    assert str(tmp_path) not in answer["path"]

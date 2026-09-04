"""`--version` describes the CLI binary; it could not describe what `import` gets.

On a box with both a pipx snapshot and an editable checkout, two different
codebases answer under one name in one shell:

    empirica --version           -> Mode: copy (pipx snapshot)
    python3 -c "import empirica" -> the checkout on develop

So every python-level check — pytest, a subprocess probe, an MCP server
importing empirica — can exercise the OTHER codebase from the one the Mode line
just described. Two practitioners hit it from opposite sides within days: one
verified uncommitted fixes through the snapshot CLI and read pre-fix behaviour
as the fix failing; the other diagnosed against the checkout while the CLI ran
the release.

WARN, never fail — editable-import beside snapshot-CLI is a normal dev state,
and not knowing is the whole defect.
"""

from __future__ import annotations

import subprocess

import pytest

from empirica.cli.cli_core import _import_path_divergence


class _Probe:
    """Stand-in for `subprocess.run` on the ambient interpreter."""

    def __init__(self, stdout="", returncode=0, raises=None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises

    def __call__(self, *a, **k):
        if self.raises:
            raise self.raises
        return subprocess.CompletedProcess(a[0] if a else [], self.returncode, self.stdout, "")


@pytest.fixture
def ambient(monkeypatch):
    """Force a DIFFERENT ambient interpreter than the running one, so the
    early-return (`same executable, nothing to disagree with`) does not mask
    what these tests are actually checking."""
    import empirica.cli.cli_core as cc

    monkeypatch.setattr("shutil.which", lambda _n: "/usr/bin/python3-other")
    return cc


def test_a_divergent_import_path_WARNS(ambient, monkeypatch):
    """THE regression, constructed rather than inherited from box state: the CLI
    lives in a pipx venv and the ambient python imports a checkout."""
    monkeypatch.setattr(subprocess, "run", _Probe(stdout="/home/dev/empirical-ai/empirica/empirica/__init__.py"))

    msg = _import_path_divergence("/home/dev/.local/share/pipx/venvs/empirica/lib/python3.14/site-packages")

    assert msg is not None
    assert "DIVERGES" in msg
    assert "/home/dev/empirical-ai/empirica" in msg


def test_an_agreeing_import_path_says_NOTHING(ambient, monkeypatch):
    """NEGATIVE CONTROL, and the common case. A warning on every invocation
    would train the same dismissal the over-firing completion nudge did."""
    root = "/home/dev/empirical-ai/empirica"
    monkeypatch.setattr(subprocess, "run", _Probe(stdout=f"{root}/empirica/__init__.py"))

    assert _import_path_divergence(root) is None


def test_an_ambient_python_that_cannot_import_empirica_is_not_a_divergence(ambient, monkeypatch):
    """A bare system python with no empirica installed is the normal state on a
    pipx-only box. Reporting that as divergence would be a false positive on
    exactly the seats that have nothing wrong."""
    monkeypatch.setattr(subprocess, "run", _Probe(stdout="", returncode=1))

    assert _import_path_divergence("/anything") is None


def test_the_same_interpreter_short_circuits(monkeypatch):
    """When the ambient python IS this interpreter there is nothing to compare,
    and the probe must not be spawned at all — `--version` is a diagnostic, not
    a place to pay for a subprocess with no question to answer."""
    import sys

    monkeypatch.setattr("shutil.which", lambda _n: sys.executable)

    def _must_not_run(*a, **k):
        raise AssertionError("probe spawned when ambient python is this interpreter")

    monkeypatch.setattr(subprocess, "run", _must_not_run)

    assert _import_path_divergence("/anything") is None


def test_a_hanging_or_broken_probe_stays_quiet(ambient, monkeypatch):
    """Fail-soft and bounded. A diagnostic that raises or hangs is worse than one
    that says nothing — this must never be able to break `--version`."""
    monkeypatch.setattr(subprocess, "run", _Probe(raises=subprocess.TimeoutExpired("python3", 5)))
    assert _import_path_divergence("/anything") is None

    monkeypatch.setattr(subprocess, "run", _Probe(raises=OSError("no such interpreter")))
    assert _import_path_divergence("/anything") is None


def test_version_output_survives_the_probe():
    """POSITIVE CONTROL on the integration: whatever the probe does, `--version`
    still reports version, python, install and mode."""
    from empirica.cli.cli_core import _get_version

    out = _get_version()

    assert "Install:" in out
    assert "Mode:" in out

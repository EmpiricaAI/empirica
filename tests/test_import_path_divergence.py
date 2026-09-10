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


# ─── the cwd axis (goal f922bd13) ──────────────────────────────────────


class _CwdAwareProbe:
    """subprocess.run stand-in that answers BY the probe's cwd.

    The single-probe version of this check ran in the CURRENT cwd, where `''`
    on sys.path makes a checkout shadow site-packages — so standing in a
    checkout it compared the CLI against the checkout and reported an
    agreement that holds only from that directory. Measured on a real box: the
    same interpreter imported the checkout from the repo root and a vendored
    copy from /tmp.
    """

    def __init__(self, by_cwd: dict):
        self.by_cwd = by_cwd  # cwd -> stdout ('' = import fails)

    def __call__(self, *a, **k):
        out = self.by_cwd.get(k.get("cwd"), "")
        return subprocess.CompletedProcess(a[0] if a else [], 0 if out else 1, out, "")


def test_cwd_dependent_resolution_warns_with_both_paths(ambient, monkeypatch):
    """The actionable, previously-silent state: HERE resolves differently
    than ANYWHERE ELSE. The warning must show both, or the reader cannot
    know which of their measurements to distrust."""
    monkeypatch.setattr(
        "subprocess.run",
        _CwdAwareProbe(
            {
                None: "/home/dev/checkout/empirica/__init__.py",  # from here
                "/": "/opt/venv/site-packages/empirica/__init__.py",  # from anywhere
            }
        ),
    )
    msg = ambient._import_path_divergence("/opt/venv/site-packages")
    assert msg is not None, "cwd-dependent resolution reported as agreement — the original bug"
    assert "CWD-DEPENDENT" in msg
    assert "/home/dev/checkout" in msg and "/opt/venv/site-packages" in msg


def test_shadow_only_import_warns(ambient, monkeypatch):
    """Importable HERE and nowhere else — the classic standing-in-a-checkout
    state. Every python-level check passes in this directory and fails on any
    other box or cwd."""
    monkeypatch.setattr(
        "subprocess.run",
        _CwdAwareProbe({None: "/home/dev/checkout/empirica/__init__.py", "/": ""}),
    )
    msg = ambient._import_path_divergence("/opt/venv/site-packages")
    assert msg is not None
    assert "ONLY from this directory" in msg


def test_agreeing_resolutions_judge_divergence_from_the_neutral_probe(ambient, monkeypatch):
    """When both cwds resolve the SAME root, behaviour must match the original
    check: warn iff that stable root differs from the CLI's install path. The
    neutral probe is the judge — not the cwd-relative one."""
    same = {"None": None}  # noqa: F841 — clarity only
    monkeypatch.setattr(
        "subprocess.run",
        _CwdAwareProbe(
            {
                None: "/opt/venv/site-packages/empirica/__init__.py",
                "/": "/opt/venv/site-packages/empirica/__init__.py",
            }
        ),
    )
    assert ambient._import_path_divergence("/opt/venv/site-packages") is None
    msg = ambient._import_path_divergence("/different/install/site-packages")
    assert msg is not None and "DIVERGES" in msg


def test_neutral_only_import_is_not_cwd_dependent(ambient, monkeypatch):
    """Resolving from a neutral cwd but not from here (e.g. probing from a
    directory whose name shadows a stdlib module) is unusual but not the
    cwd-shadowing defect — judged against the neutral truth as usual."""
    monkeypatch.setattr(
        "subprocess.run",
        _CwdAwareProbe({None: "", "/": "/opt/venv/site-packages/empirica/__init__.py"}),
    )
    assert ambient._import_path_divergence("/opt/venv/site-packages") is None

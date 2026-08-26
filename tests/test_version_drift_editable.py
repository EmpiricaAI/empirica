"""The version-drift guard must not fire on editable installs.

`version_drift()` compares `empirica.__version__` (read from the source tree)
against `importlib.metadata.version` (read from dist-info, frozen at the last
recorded install). For a normal install a mismatch means a pip upgrade landed
under a running process and a restart fixes it.

Under an EDITABLE install it means the opposite: the code is current by
construction and only the metadata is stale, so a restart reloads exactly the same
state. `serve` self-exited on that false positive and `Restart=always` relaunched
it — 1849 restarts over ~4 days on David's box, ~182 in one measured hour, all from
a release bumping the source-tree version. Every seat checked (local venv, pipx,
hetzner) is editable, so this was fleet-wide on every release.

These pin the invariant that made it unsound: **editable ⇒ never report drift**,
whatever the versions say.
"""

from __future__ import annotations

import json

import pytest

from empirica.core import version_drift as vd


@pytest.fixture(autouse=True)
def _clear_editable_cache():
    """`is_editable_install` is lru_cached (the negative path enumerates every file
    in the distribution — 5.5ms vs 0.07ms, and it runs on a watch loop). The cache
    is per-process, so tests that patch the underlying metadata must clear it on
    both sides or they leak verdicts into each other."""
    vd.is_editable_install.cache_clear()
    yield
    vd.is_editable_install.cache_clear()


class _FakePath(str):
    """dist.files entries expose `.name`; str carries the rest."""

    @property
    def name(self) -> str:
        return str(self).rsplit("/", 1)[-1]


class _FakeDist:
    def __init__(self, direct_url: str | None = None, files: list[str] | None = None):
        self._direct_url = direct_url
        self.files = [_FakePath(f) for f in (files or [])]

    def read_text(self, name: str) -> str | None:
        return self._direct_url if name == "direct_url.json" else None


EDITABLE_DIRECT_URL = json.dumps({"dir_info": {"editable": True}, "url": "file:///src/empirica"})
NORMAL_DIRECT_URL = json.dumps({"url": "https://files.pythonhosted.org/empirica.whl"})


def _patch_dist(monkeypatch, dist):
    monkeypatch.setattr(vd.importlib.metadata, "distribution", lambda _n: dist)


# ── detection ─────────────────────────────────────────────────────────


def test_detects_editable_via_direct_url(monkeypatch):
    """PEP 610 `dir_info.editable` — what pip writes for `pip install -e`, and what
    all three real seats reported."""
    _patch_dist(monkeypatch, _FakeDist(direct_url=EDITABLE_DIRECT_URL))
    assert vd.is_editable_install() is True


def test_detects_editable_via_pth_marker_when_direct_url_missing(monkeypatch):
    """Fallback for an install with no/unreadable direct_url.json. Deliberate: a
    false NOT-editable restores the restart storm, while a false editable only
    disables a self-heal whose drift is still surfaced on /health."""
    _patch_dist(monkeypatch, _FakeDist(direct_url=None, files=["__editable__.empirica-1.2.3.pth"]))
    assert vd.is_editable_install() is True


def test_detects_editable_when_direct_url_is_malformed(monkeypatch):
    _patch_dist(monkeypatch, _FakeDist(direct_url="{not json", files=["__editable___empirica_finder.py"]))
    assert vd.is_editable_install() is True


def test_detects_editable_via_egg_info_source_tree(monkeypatch):
    """`.egg-info` means the metadata came from a SOURCE TREE, not an installed
    `.dist-info`. This is the load-bearing case, not an edge case: a process started
    with the repo on sys.path — how the daemon runs from a dev checkout — resolves
    `empirica.egg-info` in the working directory, which has neither
    `direct_url.json` nor `__editable__` entries."""

    class _EggInfoDist(_FakeDist):
        _path = "/home/dev/empirica/empirica.egg-info"

    _patch_dist(monkeypatch, _EggInfoDist(direct_url=None, files=["empirica/__init__.py"]))
    assert vd.is_editable_install() is True


def test_dist_info_path_alone_does_not_imply_editable(monkeypatch):
    """The mirror of the above — a real installed distribution must stay
    non-editable, or the guard is disabled everywhere it is actually sound."""

    class _DistInfoDist(_FakeDist):
        _path = "/usr/lib/python3/site-packages/empirica-1.2.3.dist-info"

    _patch_dist(monkeypatch, _DistInfoDist(direct_url=NORMAL_DIRECT_URL, files=["empirica/__init__.py"]))
    assert vd.is_editable_install() is False


def test_normal_install_is_not_editable(monkeypatch):
    _patch_dist(monkeypatch, _FakeDist(direct_url=NORMAL_DIRECT_URL, files=["empirica/__init__.py"]))
    assert vd.is_editable_install() is False


def test_detection_never_raises(monkeypatch):
    """A detection failure must not break the caller's drift check."""

    def boom(_n):
        raise RuntimeError("no metadata")

    monkeypatch.setattr(vd.importlib.metadata, "distribution", boom)
    assert vd.is_editable_install() is False


# ── the regression ────────────────────────────────────────────────────


def test_editable_never_reports_drift_even_when_versions_differ(monkeypatch):
    """THE regression. Differing versions under editable is the NORMAL state after
    any release — reporting it caused 1849 restarts."""
    monkeypatch.setattr(vd, "is_editable_install", lambda *_a, **_k: True)
    monkeypatch.setattr(vd.importlib.metadata, "version", lambda _n: "0.0.1-ancient")
    assert vd.version_drift() is None


def test_non_editable_still_reports_real_drift(monkeypatch):
    """The guard must keep working where it is sound — a pip upgrade under a
    running process is a genuine stale-code condition."""
    monkeypatch.setattr(vd, "is_editable_install", lambda *_a, **_k: False)
    monkeypatch.setattr(vd.importlib.metadata, "version", lambda _n: "99.99.99")

    drift = vd.version_drift()
    assert drift is not None
    in_process, installed = drift
    assert installed == "99.99.99"
    assert in_process != installed


def test_non_editable_matching_versions_report_no_drift(monkeypatch):
    from empirica import __version__ as current

    monkeypatch.setattr(vd, "is_editable_install", lambda *_a, **_k: False)
    monkeypatch.setattr(vd.importlib.metadata, "version", lambda _n: current)
    assert vd.version_drift() is None


def test_drift_check_never_raises(monkeypatch):
    def boom(_n):
        raise RuntimeError("dist-info gone")

    monkeypatch.setattr(vd, "is_editable_install", lambda *_a, **_k: False)
    monkeypatch.setattr(vd.importlib.metadata, "version", boom)
    assert vd.version_drift() is None


def test_this_repo_is_editable_and_therefore_quiet():
    """Integration guard against the real environment: the dev checkout IS an
    editable install, so it must never self-report drift. If this fails, the
    detection has regressed against a real dist-info rather than a fake one."""
    if not vd.is_editable_install():
        pytest.skip("not an editable checkout (packaged/CI wheel) — nothing to assert")
    assert vd.version_drift() is None


# ── the circuit breaker ───────────────────────────────────────────────


def test_generated_listener_unit_bounds_restarts():
    """`Restart=always` with a FIXED interval is a respawn wrapper by another name.

    This test used to assert `StartLimitBurst=` and was green while the fleet
    stormed — it is named for the property and asserted a mechanism that does not
    deliver it. At the default 10s window and RestartSec=5 that is 2 starts per
    window against a burst of 5: it never trips. Units deployed on a live box
    carried no StartLimit lines at all.

    So the assertion is now on the property — the interval GROWS and is capped —
    rather than on the presence of a line that reads like protection.
    """
    from empirica.core.loop_scheduler.persistent_listener import _SYSTEMD_LISTENER_TEMPLATE as unit

    assert "Restart=always" in unit
    assert "RestartSteps=" in unit, "a fixed RestartSec is the storm cadence"
    assert "RestartMaxDelaySec=" in unit, "backoff without a cap is unbounded delay"

    # The rate limiter is disabled ON PURPOSE — layered on top of backoff it fires
    # during the fast early steps and leaves the listener silently dead.
    unit_section = unit.split("[Service]")[0]
    assert "StartLimitIntervalSec=0" in unit_section

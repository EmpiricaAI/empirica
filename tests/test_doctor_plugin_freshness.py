"""The deployed Claude Code plugin is a COPY, and nothing reported when it went stale.

`pip install -U` refreshes the package and leaves the copy untouched. The supervisor
backoff, the sentinel gate and the session-arming block all live in that copy — so a
release that fixes them fixes nothing on a box until it is synced, while every version
surface reports the new number.

Two gaps met here:

- **`doctor` is deliberately exempt from the CLI's plugin auto-heal** (it would
  re-enter), so the one verb a practitioner runs to check install health neither
  healed this nor reported it.
- **`diagnose` checks the plugin files EXIST** — presence only, so a plugin several
  minor versions behind passes. The same presence-vs-freshness gap `check_empirica_mcp`
  had already closed one check earlier in the same file.

And `cli_core` writes a `.plugin_autosync_failed` breadcrumb *specifically* so
"doctor/diagnose (and a human) surface this" — a comment naming a surface that did not
exist. A failing self-heal is worse than an absent one: the debounce marker reads
"checked" while the box keeps running old hooks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.cli.command_handlers import doctor
from empirica.cli.command_handlers.doctor import PASS, SKIP, WARN, check_plugin_freshness


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch) -> Path:
    """The check reads `~/.claude/...` and `~/.empirica/...`. Without isolation these
    tests would report on the developer's own box — green or red by accident."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return home


def _deploy(home: Path, stamp: str | None) -> Path:
    plugin = home / ".claude" / "plugins" / "local" / "empirica"
    plugin.mkdir(parents=True)
    if stamp is not None:
        (plugin / ".plugin-version").write_text(stamp + "\n")
    return plugin


@pytest.fixture
def pkg_version(monkeypatch):
    import empirica

    monkeypatch.setattr(empirica, "__version__", "9.9.9", raising=False)
    return "9.9.9"


def test_a_stale_deployed_plugin_warns(isolated_home, pkg_version):
    """THE regression. Both numbers are visible somewhere; nothing compared them."""
    _deploy(isolated_home, "1.13.30")

    check = check_plugin_freshness()

    assert check.status == WARN
    assert "1.13.30" in check.detail and "9.9.9" in check.detail, "both versions must be shown side by side"
    assert "plugin-sync" in check.hint


def test_a_fresh_deployed_plugin_passes(isolated_home, pkg_version):
    """POSITIVE CONTROL. A check that warned unconditionally would satisfy the test
    above while crying wolf on every correctly-synced box — and a check that cries
    wolf gets silenced the first time it does."""
    _deploy(isolated_home, pkg_version)

    assert check_plugin_freshness().status == PASS


def test_an_unstamped_plugin_warns_rather_than_passing(isolated_home, pkg_version):
    """A plugin predating version stamping is OLD, not unknown. Treating a missing
    stamp as 'nothing to compare' would give the most stale boxes in the fleet the
    cleanest report."""
    _deploy(isolated_home, None)

    check = check_plugin_freshness()

    assert check.status == WARN
    assert "stamp" in check.detail.lower()


def test_no_plugin_at_all_is_SKIP_not_PASS(isolated_home, pkg_version):
    """NEGATIVE CONTROL. A non-Claude-Code box is not a problem — but the comparison
    was not PERFORMED, and folding not-checked into passed is how an exemption reports
    clean forever."""
    check = check_plugin_freshness()

    assert check.status == SKIP
    assert "nothing to compare" in check.detail


def test_a_failed_autosync_breadcrumb_is_surfaced(isolated_home, pkg_version):
    """THE second gap. `cli_core` writes this marker so doctor can surface it, and
    nothing did. It takes priority over the version comparison: a self-heal that
    ERRORED is a different fact from being one release behind, and the box may look
    version-fresh while the sync that would have made it so failed."""
    _deploy(isolated_home, pkg_version)
    marker = isolated_home / ".empirica" / ".plugin_autosync_failed"
    marker.parent.mkdir(parents=True)
    marker.write_text("1788000000\trc=1\tpermission denied writing hooks/\n")

    check = check_plugin_freshness()

    assert check.status == WARN
    assert "permission denied" in check.detail, "the recorded reason must reach the reader"
    assert check.data["autosync_failed"] is True


def test_it_reports_and_never_heals(isolated_home, pkg_version, monkeypatch):
    """`doctor` is exempt from the auto-heal by design. A diagnostic that repairs what
    it measures cannot tell you what it found — and this one would re-enter the CLI it
    was invoked from."""
    calls = []
    monkeypatch.setattr(doctor, "_run", lambda *a, **k: calls.append(a) or (0, "", ""))
    _deploy(isolated_home, "1.13.30")

    check_plugin_freshness()

    assert calls == [], "the check must not shell out to plugin-sync"

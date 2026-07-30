"""`doctor` must notice a STALE empirica-mcp env, not just a present one.

`check_empirica_mcp` asserted only that the binary was on PATH. `empirica-mcp` is
normally its own isolated environment (pipx, or a venv), so it bundles its own
`empirica` — which nothing upgrades when the main install moves. Presence-only
therefore reported PASS while the Desktop/IDE MCP path ran months-old code.

Not a hypothetical: a 2026-07-30 fleet sweep found that **every** box carrying an
`empirica-mcp` seat carried a stale one — 1.12.33, 1.12.1 and 1.8.12 against a
current 1.12.38 — and `doctor` had been reporting healthy on all of them.

A check that cannot fail on a case reports clean for it forever, so these pin the
drift verdict rather than the constant.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers import doctor as D


@pytest.fixture(autouse=True)
def _mcp_on_path(monkeypatch):
    """Every test here assumes the binary exists; absence is its own case below."""
    monkeypatch.setattr(D, "_which", lambda name: "/fake/bin/empirica-mcp" if name == "empirica-mcp" else None)


def test_warns_when_the_mcp_env_bundles_an_older_empirica():
    """THE regression. This is the state that shipped healthy on three machines."""
    D_bundled, D_running = "1.12.1", "1.12.38"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(D, "_mcp_bundled_empirica_version", lambda _p: D_bundled)
        mp.setattr(D, "_empirica_version_on_path", lambda: D_running)
        c = D.check_empirica_mcp()

    assert c.status == D.WARN, "a stale MCP env must not report healthy"
    assert D_bundled in c.detail and D_running in c.detail, "both versions belong in the detail"
    assert "upgrade" in (c.hint or "").lower(), "the fix must name the remedy"
    assert c.data["bundled_empirica"] == D_bundled
    assert c.data["running_empirica"] == D_running


def test_passes_and_surfaces_the_version_when_aligned():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(D, "_mcp_bundled_empirica_version", lambda _p: "1.12.38")
        mp.setattr(D, "_empirica_version_on_path", lambda: "1.12.38")
        c = D.check_empirica_mcp()

    assert c.status == D.PASS
    assert "1.12.38" in c.detail, "showing the version is what makes the PASS falsifiable"


@pytest.mark.parametrize(
    "bundled,running",
    [(None, "1.12.38"), ("1.12.38", None), (None, None)],
)
def test_unresolvable_version_degrades_to_pass_never_fails(bundled, running):
    """Introspection trouble must not fail a health check.

    An env whose interpreter cannot be located or queried is not evidence of
    staleness — reporting WARN there would train people to ignore the warning,
    which is how the original presence-only check became useless.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(D, "_mcp_bundled_empirica_version", lambda _p: bundled)
        mp.setattr(D, "_empirica_version_on_path", lambda: running)
        c = D.check_empirica_mcp()

    assert c.status == D.PASS


def test_absent_binary_still_warns_with_install_hint(monkeypatch):
    """Unchanged behaviour: empirica-mcp is optional, so absence is a WARN."""
    monkeypatch.setattr(D, "_which", lambda _name: None)
    c = D.check_empirica_mcp()

    assert c.status == D.WARN
    assert "install" in (c.hint or "").lower()
    assert c.data["path"] is None


def test_version_parser_takes_the_number_not_the_python_line():
    """`empirica --version` prints the version AND a Python line; the parser must
    pick the version token rather than the first word."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(D, "_run", lambda *a, **k: (0, "empirica 1.12.38\nPython 3.14.4", ""))
        assert D._empirica_version_on_path() == "1.12.38"


def test_version_parser_is_honest_about_failure():
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(D, "_run", lambda *a, **k: (1, "", "boom"))
        assert D._empirica_version_on_path() is None

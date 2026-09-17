"""The core/MCP check must judge the CODE, not the label on it.

`check_mcp_version_skew` compared dist-info version strings. Measured 2026-09-17 on
one box, for one package:

    __version__ in __init__.py   1.8.14
    dist-info METADATA           1.13.46
    pyproject.toml               1.13.47
    installed vs source .py      BYTE-IDENTICAL

Every string misstated the code, each in a different direction. The check WARNed,
and its remedy told the operator to force-inject a package that already held the
current code. It would equally have PASSED two matching strings over genuinely
different code.

**An identical version string is not evidence of identical code, and a differing
one is not evidence of different code.** Content is the only authority.

This check is about to run on every seat at session start, which is why it had to
stop firing by construction first: a WARN that is always there trains people to
skip the block the real one sits in.

Everything here builds its packages under `tmp_path`. Nothing reads this box.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.cli.command_handlers import doctor as D


def _pkg(root: Path, name: str, files: dict[str, str]) -> Path:
    d = root / name
    for rel, body in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return d


BASE = {"__init__.py": '__version__ = "1.0.0"\n', "server.py": "def serve():\n    return 1\n"}


# ─── the digest ───────────────────────────────────────────────────────


def test_identical_trees_share_a_digest(tmp_path):
    a, b = _pkg(tmp_path, "a", BASE), _pkg(tmp_path, "b", BASE)

    assert D.package_content_digest(a) == D.package_content_digest(b)


def test_a_version_string_bump_does_not_change_the_digest(tmp_path):
    """The digest must not smuggle the string back in.

    Between cortex's measurement ("byte-identical") and mine an hour later the trees
    differed by exactly one line — the stale `__version__` I had just corrected. A
    digest that flipped on that would have reported different CODE for a relabel.
    """
    a = _pkg(tmp_path, "a", BASE)
    b = _pkg(tmp_path, "b", {**BASE, "__init__.py": '__version__ = "9.9.9"\n'})

    assert D.package_content_digest(a) == D.package_content_digest(b)


def test_a_real_code_change_DOES_change_the_digest(tmp_path):
    """Positive control for the normalisation above.

    Without it, a digest that ignored everything would pass both tests before it.
    """
    a = _pkg(tmp_path, "a", BASE)
    b = _pkg(tmp_path, "b", {**BASE, "server.py": "def serve():\n    return 2\n"})

    assert D.package_content_digest(a) != D.package_content_digest(b)


def test_a_renamed_module_changes_the_digest(tmp_path):
    """Relative paths are hashed — same bytes under a new name is a different package."""
    a = _pkg(tmp_path, "a", BASE)
    b = _pkg(tmp_path, "b", {"__init__.py": BASE["__init__.py"], "srv.py": BASE["server.py"]})

    assert D.package_content_digest(a) != D.package_content_digest(b)


def test_pycache_is_ignored(tmp_path):
    a = _pkg(tmp_path, "a", BASE)
    b = _pkg(tmp_path, "b", {**BASE, "__pycache__/server.cpython-314.py": "junk"})

    assert D.package_content_digest(a) == D.package_content_digest(b)


def test_an_unreadable_or_empty_package_is_None_not_a_digest(tmp_path):
    """ "Could not compare" must never read as "same"."""
    (tmp_path / "empty").mkdir()

    assert D.package_content_digest(tmp_path / "empty") is None
    assert D.package_content_digest(tmp_path / "missing") is None


# ─── the check ────────────────────────────────────────────────────────


def _wire(monkeypatch, *, core: str, mcp: str, state):
    import importlib.metadata as im

    monkeypatch.setattr(im, "version", lambda name: core if name == "empirica" else mcp)
    monkeypatch.setattr(D, "_mcp_content_state", lambda: (state, {}))


def test_the_measured_case_no_longer_cries_wolf(monkeypatch):
    """Strings differ, code identical → PASS, and the label is called a label."""
    _wire(monkeypatch, core="1.13.47", mcp="1.13.46", state="identical")

    c = D.check_mcp_version_skew()

    assert c.status == D.PASS
    assert "content-identical" in c.detail
    assert "a label, not a code difference" in c.detail
    assert c.data["compared"] == "content"


def test_matching_strings_over_different_code_is_a_WARN(monkeypatch):
    """The other direction — the false PASS the old check could not avoid."""
    _wire(monkeypatch, core="1.13.47", mcp="1.13.47", state="different")

    c = D.check_mcp_version_skew()

    assert c.status == D.WARN
    assert "DIFFERS in content" in c.detail
    assert "PIN the version" in c.hint, "the unpinned remedy once dragged core BACKWARDS"


def test_without_a_checkout_it_falls_back_to_strings_and_still_warns(monkeypatch):
    """A released-copy seat has no source to compare against. GH #404 — a 1.13.1
    server behind a 1.13.7 CLI — is a real incident and must still be caught there."""
    _wire(monkeypatch, core="1.13.7", mcp="1.13.1", state=None)

    c = D.check_mcp_version_skew()

    assert c.status == D.WARN
    assert "1.13.1" in c.detail
    assert c.data.get("compared") != "content", "must not imply content was examined"


def test_without_a_checkout_matching_strings_pass(monkeypatch):
    _wire(monkeypatch, core="1.13.47", mcp="1.13.47", state=None)

    assert D.check_mcp_version_skew().status == D.PASS


@pytest.mark.parametrize("state", ["identical", "different"])
def test_content_decides_even_when_strings_disagree_with_it(monkeypatch, state):
    """Content is the authority in BOTH directions, whatever the strings say."""
    _wire(monkeypatch, core="1.0.0", mcp="2.0.0", state=state)

    assert D.check_mcp_version_skew().status == (D.PASS if state == "identical" else D.WARN)

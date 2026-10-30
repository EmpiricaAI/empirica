"""Hooks say which core they need, and warn when the installed core is older (David, 2026-10-28; ecodex's question 5, goal 8c106034).

`session-end-postflight.py` imports `restore_if_swapped`, absent before 1.14.15, so under an older core every SessionEnd printed an import failure
with no hint of the cause. `lib/hook_vintage.py` carries the version the hooks were released with; SessionStart compares it to the installed core.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "empirica/plugins/claude-code-integration/lib"
HOOKS = ROOT / "empirica/plugins/claude-code-integration/hooks"


@pytest.fixture(scope="module")
def vintage():
    spec = importlib.util.spec_from_file_location("hook_vintage_under_test", LIB / "hook_vintage.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("installed", "vintage_", "older"),
    [
        ("1.14.15", "1.14.18", True),
        ("1.9.0", "1.14.0", True),  # compared as numbers, not text
        ("1.14.18", "1.14.18", False),
        ("1.15.0", "1.14.18", False),
        ("1.14.18.dev3", "1.14.18", False),  # a pre-release of the same X.Y.Z is not older
        ("1.14.17.dev3", "1.14.18", True),
        ("v1.14.15", "1.14.18", True),
    ],
)
def test_the_comparison_is_numeric_and_strict(vintage, installed, vintage_, older):
    assert vintage.core_older_than_hooks(installed, vintage_) is older


@pytest.mark.parametrize("bad", [None, "", "unknown", 5, "1.14", ["1", "14", "15"]])
def test_a_version_it_cannot_read_is_never_a_reason_to_warn(vintage, bad):
    assert vintage.core_older_than_hooks(bad, "1.14.18") is False
    assert vintage.core_older_than_hooks("1.14.15", bad) is False


def test_the_notice_names_both_versions_and_what_to_do_when_the_core_is_older(vintage, monkeypatch):
    monkeypatch.setattr(vintage, "_installed_core_version", lambda: "1.14.15")
    text = vintage.startup_notice()
    assert vintage.HOOK_VINTAGE in text and "1.14.15" in text and "Upgrade" in text
    assert "setup-claude-code" not in text, (
        "running setup --force on a shared box was retired as advice (David, 2026-09-18)"
    )


@pytest.mark.parametrize("installed", ["99.0.0", "2.0.0", None, "garbage"])
def test_no_notice_when_the_core_is_new_enough_or_unreadable(vintage, monkeypatch, installed):
    monkeypatch.setattr(vintage, "_installed_core_version", lambda: installed)
    assert vintage.startup_notice() == ""


def test_the_vintage_is_the_version_this_tree_releases():
    """If release.py's sweep misses the file, hooks shipped from a later tag would claim an older vintage and never warn."""
    import empirica

    text = (LIB / "hook_vintage.py").read_text()
    assert re.search(r'^HOOK_VINTAGE = "([^"]+)"', text, re.M).group(1) == empirica.__version__


def test_release_py_sweeps_and_commits_the_vintage_file():
    release = (ROOT / "scripts/release.py").read_text()
    assert release.count("hook_vintage.py") >= 2, "it must be in version_files AND in _VERSION_COMMIT_PATHS"


def test_session_start_puts_the_notice_in_the_banner_and_the_injected_context():
    """The hook itself calls the notice builder (a refactor that drops the call would leave the module green and the warning gone)."""
    source = (HOOKS / "session-init.py").read_text()
    assert "from hook_vintage import startup_notice" in source and "_core_version_notice()" in source
    top_level = [ln for ln in source.splitlines() if ln.startswith("from hook_vintage")]
    assert top_level == [], (
        "a top-level import would take SessionStart down on a harness that vendors the hook without this module"
    )
    assert "deploy_gap_text = _startup_notices(project_root)" in source, "main() must show the combined notices"


def test_session_init_builds_the_notice_through_the_library(monkeypatch):
    sys.path.insert(0, str(LIB))
    sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location("session_init_vintage", HOOKS / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    import hook_vintage

    monkeypatch.setattr(hook_vintage, "_installed_core_version", lambda: "1.14.15")
    assert "1.14.15" in mod._core_version_notice()
    monkeypatch.setattr(hook_vintage, "_installed_core_version", lambda: hook_vintage.HOOK_VINTAGE)
    assert mod._core_version_notice() == ""


def test_the_startup_notices_put_the_version_notice_first_and_drop_empty_parts(monkeypatch):
    sys.path.insert(0, str(LIB))
    sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location("session_init_notices", HOOKS / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    monkeypatch.setattr(mod, "_core_version_notice", lambda: "version notice")
    monkeypatch.setattr(mod, "_deploy_gap_block", lambda _root: "# Deploy gaps: 2")
    both = mod._startup_notices(Path("."))
    assert both.splitlines()[0] == "version notice" and "Deploy gaps" in both
    monkeypatch.setattr(mod, "_core_version_notice", lambda: "")
    assert mod._startup_notices(Path(".")) == "# Deploy gaps: 2"
    monkeypatch.setattr(mod, "_deploy_gap_block", lambda _root: "")
    assert mod._startup_notices(Path(".")) == ""


def test_a_harness_without_the_lib_module_loses_the_notice_not_session_start(monkeypatch):
    sys.path.insert(0, str(LIB))
    sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location("session_init_no_vintage", HOOKS / "session-init.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    monkeypatch.setitem(sys.modules, "hook_vintage", None)  # makes `from hook_vintage import ...` raise ImportError
    assert mod._core_version_notice() == ""


def test_a_core_exactly_at_the_hooks_vintage_gets_no_notice(vintage, monkeypatch):
    """The release sweep rewrites HOOK_VINTAGE every release, so this must not hard-code a version."""
    monkeypatch.setattr(vintage, "_installed_core_version", lambda: vintage.HOOK_VINTAGE)
    assert vintage.startup_notice() == ""

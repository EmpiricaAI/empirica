"""`diagnose --frontend ecodex` accepts the renamed plugin on either side of the rename.

ecodex 0.157.2 renamed its bundled plugin from `empirica@nubaeon` to `empirica@empiricaAI` (cache
`~/.codex/plugins/cache/empiricaAI/empirica/<ver>/`). It migrates the config key but leaves the old
cache directory in place. The checks hardcoded the old names, so a correct 0.157.2 install reported
the plugin missing and the config key absent.

Everything is built under tmp_path with HOME pinned: nothing here reads the machine's ~/.codex.
"""

from __future__ import annotations

import json

import pytest

from empirica.cli.command_handlers.diagnose_ecodex import (
    FAIL,
    PASS,
    SKIP,
    check_ecodex_plugin_enabled_in_config,
    check_ecodex_plugin_installed,
)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


def _plugin(home, namespace, version="0.157.2"):
    root = home / ".codex" / "plugins" / "cache" / namespace / "empirica" / version
    (root / ".codex-plugin").mkdir(parents=True)
    (root / ".codex-plugin" / "plugin.json").write_text(json.dumps({"name": "empirica"}))
    return root


def _config(home, *keys):
    (home / ".codex").mkdir(exist_ok=True)
    (home / ".codex" / "config.toml").write_text("".join(f'[plugins."{k}"]\nenabled = true\n' for k in keys))


def test_the_renamed_cache_directory_passes(home):
    root = _plugin(home, "empiricaAI")

    r = check_ecodex_plugin_installed()

    assert r.status == PASS and r.data["cache_dir"] == str(root)


def test_the_legacy_cache_directory_still_passes(home):
    """Control: an install from before the rename is not broken by accepting the new name."""
    root = _plugin(home, "nubaeon", "0.1.0")

    r = check_ecodex_plugin_installed()

    assert r.status == PASS and r.data["cache_dir"] == str(root)


def test_both_cache_directories_prefer_the_new_one(home):
    """ecodex leaves the old directory in place after migrating."""
    new = _plugin(home, "empiricaAI")
    _plugin(home, "nubaeon", "9.9.9")  # a higher legacy version must not win

    assert check_ecodex_plugin_installed().data["cache_dir"] == str(new)


def test_an_empty_new_namespace_falls_back_to_the_legacy_one(home):
    (home / ".codex" / "plugins" / "cache" / "empiricaAI" / "empirica").mkdir(parents=True)
    old = _plugin(home, "nubaeon", "0.1.0")

    assert check_ecodex_plugin_installed().data["cache_dir"] == str(old)


def test_neither_directory_fails_and_names_the_new_path(home):
    r = check_ecodex_plugin_installed()

    assert r.status == FAIL and "empiricaAI" in r.detail


def test_the_renamed_config_key_passes_and_is_named(home):
    _config(home, "empirica@empiricaAI")

    r = check_ecodex_plugin_enabled_in_config()

    assert r.status == PASS and r.data["plugin_key"] == "empirica@empiricaAI"


def test_the_legacy_config_key_still_passes(home):
    _config(home, "empirica@nubaeon")

    r = check_ecodex_plugin_enabled_in_config()

    assert r.status == PASS and r.data["plugin_key"] == "empirica@nubaeon"


def test_both_config_keys_report_the_new_one(home):
    _config(home, "empirica@nubaeon", "empirica@empiricaAI")

    assert check_ecodex_plugin_enabled_in_config().data["plugin_key"] == "empirica@empiricaAI"


def test_no_empirica_key_fails_and_the_hint_names_the_new_key(home):
    _config(home, "something@else")

    r = check_ecodex_plugin_enabled_in_config()

    assert r.status == FAIL and 'empirica@empiricaAI"' in r.hint


# ── the plugin_hooks gate (ecodex, prop_zzpw6mfko5dn7ey7wn5cfd5bi4) ─────────
#
# The check demanded `[features] plugin_hooks = true`, a key current codex ignores (a removed legacy
# gate), and ecodex kept writing it only so the check passed. What decides whether hooks run is
# whether the plugin is enabled and its binary reachable.

from empirica.cli.command_handlers import diagnose_ecodex as de  # noqa: E402
from empirica.cli.command_handlers.diagnose_ecodex import WARN, check_ecodex_plugin_hooks_reachable  # noqa: E402


def _toml(home, text):
    (home / ".codex").mkdir(exist_ok=True)
    (home / ".codex" / "config.toml").write_text(text)


@pytest.fixture
def binary_on_path(monkeypatch):
    monkeypatch.setattr(de.shutil, "which", lambda name: "/usr/bin/" + name)


def test_a_current_config_without_the_plugin_hooks_key_passes(home, binary_on_path):
    """The reported case: a correct install on current codex, no [features] plugin_hooks line."""
    _toml(home, '[plugins."empirica@empiricaAI"]\nenabled = true\n')

    r = check_ecodex_plugin_hooks_reachable()

    assert r.status == PASS and "no feature gate needed" in r.detail


def test_the_legacy_plugin_key_is_accepted_too(home, binary_on_path):
    _toml(home, '[plugins."empirica@nubaeon"]\nenabled = true\n')

    assert check_ecodex_plugin_hooks_reachable().status == PASS


def test_a_disabled_plugin_fails_because_its_hooks_do_not_run(home, binary_on_path):
    """CONTROL: the check still fails for the thing that does stop the hooks."""
    _toml(home, '[plugins."empirica@empiricaAI"]\nenabled = false\n')

    r = check_ecodex_plugin_hooks_reachable()

    assert r.status == FAIL and "enabled = false" in r.detail


def test_an_explicit_plugin_hooks_false_still_warns_for_older_codex(home, binary_on_path):
    _toml(home, '[features]\nplugin_hooks = false\n\n[plugins."empirica@empiricaAI"]\nenabled = true\n')

    assert check_ecodex_plugin_hooks_reachable().status == WARN


def test_plugin_hooks_true_is_harmless(home, binary_on_path):
    _toml(home, '[features]\nplugin_hooks = true\n\n[plugins."empirica@empiricaAI"]\nenabled = true\n')

    assert check_ecodex_plugin_hooks_reachable().status == PASS


def test_a_missing_binary_warns(home, monkeypatch):
    monkeypatch.setattr(de.shutil, "which", lambda name: None)
    _toml(home, '[plugins."empirica@empiricaAI"]\nenabled = true\n')

    r = check_ecodex_plugin_hooks_reachable()

    assert r.status == WARN and "not on PATH" in r.detail


def test_no_plugin_section_defers_to_the_enabled_check(home, binary_on_path):
    _toml(home, "[features]\nplugins = true\n")

    assert check_ecodex_plugin_hooks_reachable().status == SKIP


def test_invalid_toml_is_a_warning_not_a_crash(home, binary_on_path):
    _toml(home, "[plugins\nbroken")

    assert check_ecodex_plugin_hooks_reachable().status == WARN


def test_no_config_is_a_skip(home):
    assert check_ecodex_plugin_hooks_reachable().status == SKIP

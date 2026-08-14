"""Tests for `empirica plugin-sync` — the deploy-staleness auto-heal.

Closes the defect class behind the recovery-verb 'Rushed assessment' deadlock:
hook fixes land in the package on upgrade but the installed ~/.claude plugin
copy only refreshes on a manual setup-claude-code. plugin-sync re-syncs the
installed copy when its version stamp drifts behind the running empirica, and
session-init shells out to it at SessionStart.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import setup_claude_code as scc


@pytest.fixture
def fake_source(tmp_path):
    """A bundled-plugin source dir with a hooks/ file."""
    src = tmp_path / "src" / "claude-code-integration"
    (src / "hooks").mkdir(parents=True)
    (src / "hooks" / "sentinel-gate.py").write_text("# v2 gate (fixed)\n")
    (src / "plugin.json").write_text("{}\n")
    return src


@pytest.fixture
def fake_install(tmp_path):
    """An installed plugin dir with a STALE hook + no version stamp."""
    inst = tmp_path / "install" / "empirica"
    (inst / "hooks").mkdir(parents=True)
    (inst / "hooks" / "sentinel-gate.py").write_text("# v1 gate (stale)\n")
    return inst


def _wire(monkeypatch, install_dir, source_dir, version="1.12.4"):
    monkeypatch.setattr(scc, "_resolve_empirica_version", lambda: version)
    monkeypatch.setattr(scc, "_installed_plugin_dir", lambda: install_dir)
    monkeypatch.setattr(scc, "_get_plugin_source_dir", lambda: source_dir)


def _run(output="json", **over):
    args = SimpleNamespace(output=output, force=False, quiet=False)
    for k, v in over.items():
        setattr(args, k, v)
    return scc.handle_plugin_sync_command(args)


# ── drift → sync ─────────────────────────────────────────────────────────


def test_drift_unstamped_install_syncs(monkeypatch, capsys, fake_install, fake_source):
    """An install with no version stamp is treated as drift → re-synced."""
    _wire(monkeypatch, fake_install, fake_source)
    rc = _run()
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["synced"] is True and out["to"] == "1.12.4"
    # the stale hook was overwritten with the fixed source version
    assert (fake_install / "hooks" / "sentinel-gate.py").read_text() == "# v2 gate (fixed)\n"
    # stamp written
    assert (fake_install / scc.PLUGIN_VERSION_STAMP).read_text().strip() == "1.12.4"


def test_drift_stale_stamp_syncs(monkeypatch, capsys, fake_install, fake_source):
    (fake_install / scc.PLUGIN_VERSION_STAMP).write_text("1.11.2\n")  # behind
    _wire(monkeypatch, fake_install, fake_source)
    out = json.loads((_run(), capsys.readouterr().out)[1])
    assert out["synced"] is True and out["from"] == "1.11.2" and out["to"] == "1.12.4"
    assert (fake_install / "hooks" / "sentinel-gate.py").read_text() == "# v2 gate (fixed)\n"


# ── no drift → no-op ─────────────────────────────────────────────────────


def test_matching_stamp_is_noop(monkeypatch, capsys, fake_install, fake_source):
    (fake_install / scc.PLUGIN_VERSION_STAMP).write_text("1.12.4\n")
    _wire(monkeypatch, fake_install, fake_source)
    out = json.loads((_run(), capsys.readouterr().out)[1])
    assert out["synced"] is False and out["reason"] == "current"
    # stale-marker file untouched (no copy happened)
    assert (fake_install / "hooks" / "sentinel-gate.py").read_text() == "# v1 gate (stale)\n"


def test_force_syncs_even_when_current(monkeypatch, capsys, fake_install, fake_source):
    (fake_install / scc.PLUGIN_VERSION_STAMP).write_text("1.12.4\n")
    _wire(monkeypatch, fake_install, fake_source)
    out = json.loads((_run(force=True), capsys.readouterr().out)[1])
    assert out["synced"] is True
    assert (fake_install / "hooks" / "sentinel-gate.py").read_text() == "# v2 gate (fixed)\n"


# ── safety / edge cases ──────────────────────────────────────────────────


def test_no_install_is_not_an_error(monkeypatch, capsys, tmp_path, fake_source):
    missing = tmp_path / "nope" / "empirica"
    _wire(monkeypatch, missing, fake_source)
    rc = _run()
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True and out["synced"] is False and out["reason"] == "not_installed"


def test_missing_source_errors_without_clobber(monkeypatch, capsys, fake_install):
    _wire(monkeypatch, fake_install, None)  # source not found
    rc = _run()
    out = json.loads(capsys.readouterr().out)
    assert rc == 1 and out["ok"] is False and out["reason"] == "source_not_found"
    # install left intact
    assert (fake_install / "hooks" / "sentinel-gate.py").read_text() == "# v1 gate (stale)\n"


def test_unknown_version_does_not_falsely_match(monkeypatch, capsys, fake_install, fake_source):
    """If the running version can't be resolved ('unknown'), don't treat a
    coincidental 'unknown' stamp as current — sync to be safe."""
    (fake_install / scc.PLUGIN_VERSION_STAMP).write_text("unknown\n")
    _wire(monkeypatch, fake_install, fake_source, version="unknown")
    out = json.loads((_run(), capsys.readouterr().out)[1])
    assert out["synced"] is True


def test_sync_is_in_place_no_rmtree(monkeypatch, fake_install, fake_source):
    """The sync overwrites in place — a user file alongside the plugin is not
    wiped (only setup-claude-code --force does a full rmtree rebuild)."""
    (fake_install / "user-local-note.txt").write_text("keep me\n")
    _wire(monkeypatch, fake_install, fake_source)
    _run()
    assert (fake_install / "user-local-note.txt").read_text() == "keep me\n"
    assert (fake_install / "hooks" / "sentinel-gate.py").read_text() == "# v2 gate (fixed)\n"


def test_install_preserves_bundle_skills_not_shipped_by_source(tmp_path):
    """REGRESSION (Philipp mesh-support 2026-08-14): the wholesale rmtree+copytree
    plugin replace in _install_plugin_files must NOT drop skills the open plugin
    does not ship. The CORTEX BUNDLE installs cortex-mailbox-poll/-send,
    empirica-constitution, epistemic-transaction, before-you-assert on top of the
    open plugin's skills dir; a sync silently deleted them on every update."""
    source = tmp_path / "source"
    (source / "skills" / "open-skill").mkdir(parents=True)
    (source / "skills" / "open-skill" / "SKILL.md").write_text("open v2\n")
    (source / "plugin.json").write_text("{}\n")

    install = tmp_path / "install"
    (install / "skills" / "open-skill").mkdir(parents=True)
    (install / "skills" / "open-skill" / "SKILL.md").write_text("open v1 (stale)\n")
    # Bundle-installed skill: present in the install, absent from source.
    (install / "skills" / "cortex-mailbox-poll").mkdir(parents=True)
    (install / "skills" / "cortex-mailbox-poll" / "SKILL.md").write_text("bundle content\n")

    scc._install_plugin_files(source, install, "json")

    # The bundle skill survived the replace...
    assert (install / "skills" / "cortex-mailbox-poll" / "SKILL.md").read_text() == "bundle content\n"
    # ...and the open plugin's own skill was refreshed from source.
    assert (install / "skills" / "open-skill" / "SKILL.md").read_text() == "open v2\n"


def test_install_no_foreign_skills_is_clean(tmp_path):
    """When the install has only skills the source ships, nothing extra is kept
    and the replace refreshes normally (no false preservation)."""
    source = tmp_path / "source"
    (source / "skills" / "open-skill").mkdir(parents=True)
    (source / "skills" / "open-skill" / "SKILL.md").write_text("open v2\n")
    (source / "plugin.json").write_text("{}\n")

    install = tmp_path / "install"
    (install / "skills" / "open-skill").mkdir(parents=True)
    (install / "skills" / "open-skill" / "SKILL.md").write_text("open v1\n")

    scc._install_plugin_files(source, install, "json")
    assert (install / "skills" / "open-skill" / "SKILL.md").read_text() == "open v2\n"
    assert sorted(p.name for p in (install / "skills").iterdir()) == ["open-skill"]

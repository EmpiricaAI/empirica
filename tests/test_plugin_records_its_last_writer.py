"""The shared plugin copy records which practice deployed it, and doctor says so.

Every practice on a box deploys into one user-global plugin directory with no
lock, so one practice can revert another's deploy and the version stamp alone
cannot say whose write it is (empirica-mesh-support). Built under tmp_path.
"""

from __future__ import annotations

import json

from empirica.cli.command_handlers import doctor
from empirica.cli.command_handlers import setup_claude_code as scc


def _practice(tmp_path, ai_id):
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text(f"ai_id: {ai_id}\n")
    return proj


def test_every_deploy_writes_who_what_and_when(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.chdir(_practice(tmp_path, "empirica-outreach"))
    scc._write_plugin_version_stamp(plugin, "1.13.51")
    assert (plugin / ".plugin-version").read_text().strip() == "1.13.51"
    rec = json.loads((plugin / ".plugin-writer.json").read_text())
    assert rec["ai_id"] == "empirica-outreach" and rec["version"] == "1.13.51" and rec["written_at"]


def test_positive_control_no_project_means_an_unknown_writer_not_a_wrong_one(tmp_path, monkeypatch):
    """When NO source knows the practice, the record says so rather than inventing one.

    The package location is pinned away from the real checkout here: otherwise
    this asserts something about the developer's tree, and after the
    source_checkout fallback it would read core's own ai_id.
    """
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.delenv("EMPIRICA_AI_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    nowhere = tmp_path / "nowhere"
    (nowhere / "empirica" / "cli" / "command_handlers").mkdir(parents=True)
    monkeypatch.setattr(scc, "__file__", str(nowhere / "empirica" / "cli" / "command_handlers" / "setup.py"))
    monkeypatch.setattr("empirica.utils.session_resolver.InstanceResolver.ai_id", staticmethod(lambda: None))
    scc._write_plugin_version_stamp(plugin, "1.13.51")
    rec = json.loads((plugin / ".plugin-writer.json").read_text())
    assert rec["ai_id"] is None and rec["ai_id_source"] == "unknown"


def test_doctor_names_the_writer_on_a_stale_deploy(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin = home / ".claude" / "plugins" / "local" / "empirica"
    plugin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(_practice(tmp_path, "empirica-autonomy"))
    scc._write_plugin_version_stamp(plugin, "0.0.1")  # older than any real package

    check = doctor.check_plugin_freshness()
    assert check.status == doctor.WARN
    assert "written by empirica-autonomy" in check.detail
    assert check.data["last_writer"]["ai_id"] == "empirica-autonomy"


def test_an_unreadable_record_is_none():
    from pathlib import Path

    assert doctor._plugin_writer(Path("/nonexistent")) is None


# ── Who deployed, when the deploy did not run from a practice checkout ────────
# cortex, prop_xjnwejgcnzetdozzlja54kf2ca: fleet deploys run from $HOME, so the
# cwd-only lookup stamped `ai_id: null` and the record named nobody.
def test_the_env_var_wins_and_says_so(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.chdir(_practice(tmp_path, "empirica-outreach"))
    monkeypatch.setenv("EMPIRICA_AI_ID", "empirica-autonomy")
    scc._write_plugin_version_stamp(plugin, "1.13.51")
    rec = json.loads((plugin / ".plugin-writer.json").read_text())
    assert rec["ai_id"] == "empirica-autonomy" and rec["ai_id_source"] == "env"


def test_a_deploy_from_a_neutral_directory_falls_back_to_the_source_checkout(tmp_path, monkeypatch):
    """The $HOME case. The package is the practice's own editable install, so the
    checkout the plugin files came from names the practice when the cwd cannot."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    neutral = tmp_path / "home"
    neutral.mkdir()
    monkeypatch.delenv("EMPIRICA_AI_ID", raising=False)
    monkeypatch.chdir(neutral)
    checkout = _practice(tmp_path, "empirica-mesh-support")
    monkeypatch.setattr(scc, "__file__", str(checkout / "empirica" / "cli" / "command_handlers" / "setup.py"))
    scc._write_plugin_version_stamp(plugin, "1.13.51")
    rec = json.loads((plugin / ".plugin-writer.json").read_text())
    assert rec["ai_id"] == "empirica-mesh-support" and rec["ai_id_source"] == "source_checkout"


def test_cwd_still_wins_over_the_source_checkout(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.delenv("EMPIRICA_AI_ID", raising=False)
    monkeypatch.chdir(_practice(tmp_path, "empirica-outreach"))
    scc._write_plugin_version_stamp(plugin, "1.13.51")
    rec = json.loads((plugin / ".plugin-writer.json").read_text())
    assert rec["ai_id"] == "empirica-outreach" and rec["ai_id_source"] == "cwd"


def test_doctor_says_an_inferred_writer_was_inferred(tmp_path, monkeypatch):
    home = tmp_path / "home"
    plugin = home / ".claude" / "plugins" / "local" / "empirica"
    plugin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (plugin / ".plugin-version").write_text("0.0.1\n")
    (plugin / ".plugin-writer.json").write_text(
        json.dumps({"ai_id": "empirica-mesh-support", "ai_id_source": "source_checkout", "written_at": "2026-09-23"})
    )
    check = doctor.check_plugin_freshness()
    assert "written by empirica-mesh-support via source_checkout" in check.detail


def test_doctor_does_not_annotate_a_writer_that_named_itself(tmp_path, monkeypatch):
    """Positive control for the annotation: `cwd` and `env` print unadorned."""
    home = tmp_path / "home"
    plugin = home / ".claude" / "plugins" / "local" / "empirica"
    plugin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (plugin / ".plugin-version").write_text("0.0.1\n")
    (plugin / ".plugin-writer.json").write_text(
        json.dumps({"ai_id": "empirica-outreach", "ai_id_source": "cwd", "written_at": "2026-09-23"})
    )
    check = doctor.check_plugin_freshness()
    assert "written by empirica-outreach at" in check.detail and "via" not in check.detail

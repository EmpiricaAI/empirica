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
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.chdir(tmp_path)
    scc._write_plugin_version_stamp(plugin, "1.13.51")
    assert json.loads((plugin / ".plugin-writer.json").read_text())["ai_id"] is None


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

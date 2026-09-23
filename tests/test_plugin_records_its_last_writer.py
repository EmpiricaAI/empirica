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


def test_a_deploy_from_a_neutral_directory_names_the_OPERATOR_not_a_practice(tmp_path, monkeypatch):
    """The $HOME case, corrected.

    The first fix read the checkout the package came from. mesh-support refuted
    it (prop_zxpwbp7aibcgdbbzk7plmh3n4u): a fleet deploy is run by an operator,
    and on a box with nine practices there is no right practice to name. On this
    box the package is core's checkout, so that fallback would have attributed
    every operator deploy to core — a plausible wrong name, worse than none.
    """
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
    assert rec["ai_id"] is None and rec["ai_id_source"] == "unknown"
    assert "@" in rec["deployed_by"], "the operator is always recorded"


def test_an_explicit_ai_id_is_recorded_as_explicit(tmp_path, monkeypatch):
    """The one case where a practice name is true rather than guessed: a caller
    that knows passes it (mesh-support's ecosystem-update, via --ai-id)."""
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.delenv("EMPIRICA_AI_ID", raising=False)
    monkeypatch.chdir(tmp_path)
    scc._write_plugin_version_stamp(plugin, "1.13.51", ai_id="empirica-cortex")
    rec = json.loads((plugin / ".plugin-writer.json").read_text())
    assert rec["ai_id"] == "empirica-cortex" and rec["ai_id_source"] == "explicit"


def test_cwd_names_the_practice_when_the_deploy_ran_from_its_checkout(tmp_path, monkeypatch):
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.delenv("EMPIRICA_AI_ID", raising=False)
    monkeypatch.chdir(_practice(tmp_path, "empirica-outreach"))
    scc._write_plugin_version_stamp(plugin, "1.13.51")
    rec = json.loads((plugin / ".plugin-writer.json").read_text())
    assert rec["ai_id"] == "empirica-outreach" and rec["ai_id_source"] == "cwd"


def test_doctor_names_the_operator_when_no_practice_was_recorded(tmp_path, monkeypatch):
    """Absence of a practice must not print as '?' where a practice is expected:
    that is the shape that makes an unknown read as a fact about the writer."""
    home = tmp_path / "home"
    plugin = home / ".claude" / "plugins" / "local" / "empirica"
    plugin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (plugin / ".plugin-version").write_text("0.0.1\n")
    (plugin / ".plugin-writer.json").write_text(
        json.dumps(
            {
                "ai_id": None,
                "ai_id_source": "unknown",
                "deployed_by": "ops@fleet-runner",
                "written_at": "2026-09-23",
            }
        )
    )
    check = doctor.check_plugin_freshness()
    assert "written by ops@fleet-runner at" in check.detail and "via" not in check.detail


def test_doctor_annotates_a_name_derived_from_the_directory(tmp_path, monkeypatch):
    """`cwd` is where the command ran, not an assertion by the deployer — and on a
    box of worktrees it resolves to a parent practice. It prints as derived."""
    home = tmp_path / "home"
    plugin = home / ".claude" / "plugins" / "local" / "empirica"
    plugin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (plugin / ".plugin-version").write_text("0.0.1\n")
    (plugin / ".plugin-writer.json").write_text(
        json.dumps({"ai_id": "empirica-outreach", "ai_id_source": "cwd", "written_at": "2026-09-23"})
    )
    check = doctor.check_plugin_freshness()
    assert "written by empirica-outreach via cwd at" in check.detail


def test_doctor_does_not_annotate_a_writer_that_named_itself(tmp_path, monkeypatch):
    """Positive control for the annotation: an asserted identity prints unadorned."""
    home = tmp_path / "home"
    plugin = home / ".claude" / "plugins" / "local" / "empirica"
    plugin.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    (plugin / ".plugin-version").write_text("0.0.1\n")
    (plugin / ".plugin-writer.json").write_text(
        json.dumps({"ai_id": "empirica-outreach", "ai_id_source": "explicit", "written_at": "2026-09-23"})
    )
    check = doctor.check_plugin_freshness()
    assert "written by empirica-outreach at" in check.detail and "via" not in check.detail

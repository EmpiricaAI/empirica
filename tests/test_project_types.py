"""One list of project types, defined once (goal 050341d2, David 2026-10-29).

Before: the vocabulary lived in about a dozen places that disagreed. Two type-to-calibration-domain maps in particular: POSTFLIGHT's had only legacy
names, so a project of type `software` (the type of most live projects) resolved to the `default` calibration domain there, while project-init's map
had only the new names. These tests pin the single source and the places that must read it.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest
import yaml

from empirica.core import project_types as pt

ROOT = Path(__file__).resolve().parents[1]
DOMAINS_DIR = ROOT / "empirica" / "config" / "domains"
WEIGHTS = ROOT / "empirica" / "config" / "mco" / "confidence_weights.yaml"


# ── the list ─────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_list_is_sixteen_distinct_kebab_case_values_with_the_agreed_software_shapes():
    assert len(pt.PROJECT_TYPES) == 16 and len(set(pt.PROJECT_TYPES)) == 16
    assert all(re.fullmatch(r"[a-z]+(-[a-z]+)*", t) for t in pt.PROJECT_TYPES)
    assert pt.SOFTWARE_TYPES == (
        "web-app",
        "service",
        "cli",
        "library",
        "desktop-app",
        "mobile-app",
        "browser-extension",
        "infrastructure",
    )
    assert "software" not in pt.PROJECT_TYPES, "software is the umbrella the shapes replace"


def test_legacy_values_are_not_in_the_list_and_are_not_accepted_as_input():
    assert not (pt.LEGACY_TYPES & set(pt.PROJECT_TYPES))
    for legacy in pt.LEGACY_TYPES:
        with pytest.raises(ValueError, match="legacy"):
            pt.validate_input(legacy)


def test_input_validation_accepts_the_list_and_none_and_refuses_everything_else_with_the_list():
    assert pt.validate_input(None) is None and pt.validate_input("") is None
    assert all(pt.validate_input(t) == t for t in pt.PROJECT_TYPES)
    with pytest.raises(ValueError, match="web-app"):
        pt.validate_input("gizmo")


def test_a_stored_legacy_value_is_still_readable_and_an_unknown_one_is_not():
    assert all(pt.is_readable(v) for v in (None, "cli", "product", "software"))
    assert not pt.is_readable("gizmo")


# ── what a type implies ──────────────────────────────────────────────────────────────────────────────────────────


def test_every_calibration_domain_a_type_maps_to_has_weights_in_the_yaml():
    """The contract with confidence_weights.yaml: a mapping to a domain it does not define would fall back to default without a word."""
    known = set(yaml.safe_load(WEIGHTS.read_text())["domain_category_weights"])
    mapped = {pt.calibration_domain(t) for t in (*pt.PROJECT_TYPES, *pt.LEGACY_TYPES)}
    assert mapped <= known, mapped - known


def test_software_shapes_use_the_software_calibration_domain_and_the_old_bug_is_gone():
    """POSTFLIGHT's map had no `software` key: type software resolved to `default` there."""
    assert pt.calibration_domain("software") == "software"
    assert all(pt.calibration_domain(t) == "software" for t in pt.SOFTWARE_TYPES if t != "infrastructure")
    assert pt.calibration_domain("infrastructure") == "operations"


def test_unclassified_and_unknown_use_the_default_calibration_domain():
    assert pt.calibration_domain(None) == "default" == pt.calibration_domain("") == pt.calibration_domain("gizmo")


def test_every_default_work_frame_names_a_real_domain_and_criticality():
    domain_files = {p.stem for p in DOMAINS_DIR.glob("*.yaml")}
    levels = set(yaml.safe_load((DOMAINS_DIR / "default.yaml").read_text())["criticalities"])
    for t in pt.PROJECT_TYPES:
        domain, criticality = pt.default_work_frame(t)
        assert domain in domain_files and criticality in levels, (t, domain, criticality)


def test_the_work_frames_the_draft_agreed():
    assert pt.default_work_frame("infrastructure") == ("default", "high")
    assert pt.default_work_frame("research") == ("research", "medium")
    assert pt.default_work_frame("engagement") == ("consulting", "medium")
    assert pt.default_work_frame("content") == ("marketing", "medium")
    assert pt.default_work_frame(None) == ("default", "medium")
    assert all(pt.default_work_frame(t)[0] != "cybersec" for t in pt.PROJECT_TYPES), (
        "security is a flag, not a project default"
    )


# ── the places that must read the one list ──────────────────────────────────────────────────────────────────────


def _parser():
    from empirica.cli.parsers.checkpoint_parsers import add_checkpoint_parsers

    parser = argparse.ArgumentParser()
    add_checkpoint_parsers(parser.add_subparsers(dest="command"))
    return parser


@pytest.mark.parametrize("verb", ["project-init", "project-update"])
def test_the_type_flag_of_init_and_update_offers_exactly_the_list(verb):
    parser = _parser()
    for t in pt.PROJECT_TYPES:
        assert parser.parse_args([verb, "--type", t]).type == t
    with pytest.raises(SystemExit):
        parser.parse_args([verb, "--type", "product"])


def test_project_create_offers_the_same_list_and_no_longer_defaults_to_product():
    parser = _parser()
    args = parser.parse_args(["project-create", "--name", "x"])
    assert args.type is None
    assert parser.parse_args(["project-create", "--name", "x", "--type", "cli"]).type == "cli"
    with pytest.raises(SystemExit):
        parser.parse_args(["project-create", "--name", "x", "--type", "application"])


def test_workspace_list_can_still_filter_rows_that_hold_a_legacy_value():
    """Until the registry rows are reclassified, `workspace-list --type product` is how a person finds them."""
    parser = _parser()
    for t in (*pt.PROJECT_TYPES, *pt.LEGACY_TYPES):
        assert parser.parse_args(["workspace-list", "--type", t]).type == t


def test_postflight_and_project_init_resolve_the_calibration_domain_through_the_module():
    import empirica.cli.command_handlers._workflow_postflight as pf
    from empirica.cli.command_handlers.project_init import _seed_calibration_weights

    assert not hasattr(pf, "_TYPE_TO_DOMAIN"), "a second map is how the two copies disagreed"
    assert _seed_calibration_weights("cli") == _seed_calibration_weights("web-app")
    assert _seed_calibration_weights("cli") != _seed_calibration_weights("research")


def test_the_project_config_loader_accepts_the_list_and_still_reads_legacy_yaml():
    from empirica.config.project_config_loader import ProjectConfig

    assert set(pt.PROJECT_TYPES) <= set(ProjectConfig.VALID_TYPES)
    assert set(pt.LEGACY_TYPES) <= set(ProjectConfig.VALID_TYPES)


# ── the repository ───────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def db(tmp_path, monkeypatch):
    from empirica.data.session_database import SessionDatabase

    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(tmp_path / "s.db"))
    database = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield database
    database.close()


def _type_of(db, project_id):
    return db.conn.execute("SELECT project_type FROM projects WHERE id = ?", (project_id,)).fetchone()[0]


def test_a_project_created_without_a_type_is_unclassified_not_silently_software(db):
    assert _type_of(db, db.create_project(name="a")) is None


def test_a_project_created_with_a_listed_type_keeps_it(db):
    assert _type_of(db, db.create_project(name="a", project_type="browser-extension")) == "browser-extension"


def test_an_unknown_type_is_refused_not_defaulted(db):
    with pytest.raises(ValueError, match="Valid types"):
        db.create_project(name="a", project_type="gizmo")


def test_a_legacy_type_from_an_existing_file_is_stored_as_given_with_a_warning(db, caplog):
    """project.yaml files written before this carry `type: software`; re-running project-init on them must not fail."""
    with caplog.at_level("WARNING"):
        pid = db.create_project(name="a", project_type="software")
    assert _type_of(db, pid) == "software"
    assert "legacy" in caplog.text.lower() and "project-update" in caplog.text


# ── the type sets a transaction's starting domain and criticality (David's ruling c: the first consumer of the work frame) ─────────


def _enrich(tmp_path, monkeypatch, project_type, parsed_domain=None, parsed_criticality=None):
    import json

    import empirica.cli.command_handlers._workflow_preflight as pre

    (tmp_path / ".empirica").mkdir()
    if project_type:
        (tmp_path / ".empirica" / "project.yaml").write_text(yaml.safe_dump({"name": "p", "type": project_type}))
    tx = tmp_path / ".empirica" / "active_transaction.json"
    tx.write_text(json.dumps({"transaction_id": "t"}))
    monkeypatch.setattr(pre.R, "instance_suffix", staticmethod(lambda: ""))
    parsed = {
        "work_context": None,
        "work_type": None,
        "domain": parsed_domain,
        "criticality": parsed_criticality,
        "predicted_check_outcomes": None,
        "task_context": "x",
    }
    pre._preflight_enrich_transaction_file(str(tmp_path), parsed)
    return json.loads(tx.read_text())


def test_a_research_project_starts_in_the_research_domain(tmp_path, monkeypatch):
    assert _enrich(tmp_path, monkeypatch, "research")["domain"] == "research"


def test_an_infrastructure_project_starts_at_high_criticality(tmp_path, monkeypatch):
    out = _enrich(tmp_path, monkeypatch, "infrastructure")
    assert out["criticality"] == "high" and "domain" not in out, "its domain is default: nothing to write"


def test_what_the_transaction_declares_wins_over_the_project_default(tmp_path, monkeypatch):
    out = _enrich(tmp_path, monkeypatch, "research", parsed_domain="cybersec", parsed_criticality="low")
    assert out["domain"] == "cybersec" and out["criticality"] == "low"


def test_a_software_shape_or_an_unclassified_project_changes_nothing(tmp_path, monkeypatch):
    for kind in ("cli", None, "software"):
        sub = tmp_path / str(kind)
        sub.mkdir()
        out = _enrich(sub, monkeypatch, kind)
        assert "domain" not in out and "criticality" not in out, kind

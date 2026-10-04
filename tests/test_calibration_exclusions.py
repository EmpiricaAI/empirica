"""Known-bad observations can be excluded from the grounded bias, and the output says so.

From 2026-08-01 to 2026-09-20 core's git evidence for do, state and change was graded over a window far wider than the
transaction (about 16-24 commits per row against 1-2 either side), fixed by d077d5697 in v1.13.51. 284 stored rows still fed
the injected bias block. David ruled 2026-10-04: exclude them from the block, keep the rows.

The grounded belief is a sequential Gaussian update, so the exclusion is a replay of that update over grounded_verifications
that skips matching observations. The replay is only trustworthy if, with no exclusions, it reproduces the stored belief; the
first tests hold that. Exclusions are practice-owned config in project.yaml, so another seat records its own window.
"""

from __future__ import annotations

import json
import time
import types

import pytest
import yaml

import empirica.core.post_test.grounded_calibration as gc
from empirica.core.post_test.grounded_calibration import (
    GroundedCalibrationManager,
    load_calibration_exclusions,
)
from empirica.core.post_test.mapper import GroundedAssessment, GroundedVectorEstimate
from empirica.data.session_database import SessionDatabase

DAY = 86400.0
T0 = 1_785_000_000.0  # fixed, so windows are unambiguous


@pytest.fixture
def world(tmp_path, monkeypatch):
    db = SessionDatabase(db_path=str(tmp_path / "sessions.db"))
    pid = db.create_project(name="p")
    sid = db.create_session(ai_id="a", project_id=pid)
    gcm = GroundedCalibrationManager(db)
    # The self-referential track needs cascades; the replay is about the grounded side, so pin it.
    monkeypatch.setattr(
        "empirica.core.bayesian_beliefs.BayesianBeliefManager.get_beliefs",
        lambda self, ai_id: {
            "change": types.SimpleNamespace(mean=0.3, evidence_count=10),
            "do": types.SimpleNamespace(mean=0.9, evidence_count=10),
        },
    )
    yield types.SimpleNamespace(db=db, gcm=gcm, sid=sid)
    db.close()


def _observe(world, vec, value, source, at, confidence=0.8, evidence=3):
    """One real verification: the real update (stored belief) plus the verification row the replay reads."""
    est = GroundedVectorEstimate(
        vector_name=vec, estimated_value=value, confidence=confidence, evidence_count=evidence, primary_source=source
    )
    assessment = GroundedAssessment(
        session_id=world.sid,
        self_assessed={vec: 0.3},
        grounded={vec: est},
        calibration_gaps={vec: 0.3 - value},
        grounded_coverage=1.0,
        overall_calibration_score=0.0,
    )
    world.gcm.update_grounded_beliefs(world.sid, assessment)
    world.db.conn.execute(
        "INSERT INTO grounded_verifications (verification_id, session_id, ai_id, self_assessed_vectors, "
        "grounded_vectors, created_at) VALUES (?, ?, 'a', ?, ?, ?)",
        (
            f"v-{vec}-{at}-{source}",
            world.sid,
            json.dumps({vec: 0.3}),
            json.dumps({vec: {"value": value, "confidence": confidence, "evidence_count": evidence, "source": source}}),
            at,
        ),
    )
    world.db.conn.commit()


def _history(world):
    """Honest rows before the window, inflated git rows (and one goals row) inside it, honest rows after, in time order."""
    rows = [(0, 0.30, "git"), (1, 0.35, "git"), (2, 0.25, "git")]
    rows += [(10 + i, 1.0, "git") for i in range(3)] + [(12.5, 1.0, "goals")] + [(13 + i, 1.0, "git") for i in range(3)]
    rows += [(30, 0.40, "git"), (31, 0.30, "git")]
    for day, value, source in rows:
        _observe(world, "change", value, source, T0 + day * DAY)


WINDOW = [
    {
        "vectors": ["change"],
        "source": "git",
        "from": T0 + 9 * DAY,
        "until": T0 + 20 * DAY,
        "reason": "git window spanned the session",
    }
]


def test_a_replay_with_no_exclusions_reproduces_the_stored_belief(world):
    """The control the rest rests on: if this fails the replay is a different quantity and nothing below means anything."""
    _history(world)
    stored = world.gcm.get_grounded_beliefs("a")["change"]

    replay = world.gcm.replay_grounded_belief("a", "change", [])

    assert replay["mean"] == pytest.approx(stored.mean, abs=1e-9)
    assert replay["evidence_count"] == stored.evidence_count
    assert replay["excluded"] == 0


def test_matching_observations_are_skipped_and_counted(world):
    _history(world)
    full = world.gcm.replay_grounded_belief("a", "change", [])
    cut = world.gcm.replay_grounded_belief("a", "change", WINDOW)

    assert cut["excluded"] == 6
    assert cut["mean"] < full["mean"], "the inflated 1.0 observations were pulling the mean up"
    assert cut["evidence_count"] == full["evidence_count"] - 6 * 3


def test_another_source_inside_the_dates_is_kept(world):
    """The exclusion names a source: the goals row sits inside the window and stays."""
    _history(world)
    only_goals = [{**WINDOW[0], "source": "goals"}]

    assert world.gcm.replay_grounded_belief("a", "change", only_goals)["excluded"] == 1


def test_window_edges_are_from_inclusive_until_exclusive(world):
    _observe(world, "change", 0.9, "git", T0 + 9 * DAY)
    _observe(world, "change", 0.9, "git", T0 + 20 * DAY)

    assert world.gcm.replay_grounded_belief("a", "change", WINDOW)["excluded"] == 1


def test_divergence_uses_the_replay_only_for_affected_vectors_and_says_so(world):
    _history(world)
    _observe(world, "do", 0.7, "git", T0 + 12 * DAY)
    plain = world.gcm.get_calibration_divergence("a")
    cut = world.gcm.get_calibration_divergence("a", exclusions=WINDOW)

    assert cut["change"]["gap"] > plain["change"]["gap"], "less inflated grounded -> self - grounded moves up"
    assert cut["change"]["excluded_observations"] == 6
    assert cut["do"] == plain["do"] and "excluded_observations" not in cut["do"]


def test_adjustments_follow_the_excluded_divergence(world):
    _history(world)
    plain = world.gcm.get_grounded_adjustments("a")
    cut = world.gcm.get_grounded_adjustments("a", exclusions=WINDOW)

    assert cut["change"] != plain["change"]


# ── config ──────────────────────────────────────────────────────────────────


def _project(tmp_path, entries):
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(
        yaml.safe_dump({"name": "x", "calibration_exclusions": entries})
    )
    return str(tmp_path)


def test_the_loader_reads_dates_as_utc_midnight_and_keeps_the_reason(tmp_path):
    root = _project(
        tmp_path,
        [{"vectors": ["do", "change"], "source": "git", "from": "2026-08-01", "until": "2026-09-21", "reason": "r"}],
    )
    (entry,) = load_calibration_exclusions(root)

    assert entry["vectors"] == ["do", "change"] and entry["source"] == "git" and entry["reason"] == "r"
    assert entry["until"] - entry["from"] == 51 * DAY


def test_a_malformed_entry_is_dropped_loudly_not_applied_wide(tmp_path, caplog):
    """An entry we cannot read must not become 'exclude everything'."""
    root = _project(tmp_path, [{"vectors": "change", "source": "git"}, {"vectors": ["change"], "from": "not-a-date"}])

    with caplog.at_level("WARNING"):
        assert load_calibration_exclusions(root) == []
    assert "calibration_exclusions" in caplog.text


def test_no_file_or_no_key_means_no_exclusions(tmp_path):
    assert load_calibration_exclusions(str(tmp_path)) == []
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text("name: x\n")
    assert load_calibration_exclusions(str(tmp_path)) == []


# ── the export says what it excluded ────────────────────────────────────────


def test_the_breadcrumbs_section_records_the_exclusions(world, tmp_path):
    _history(world)
    root = _project(tmp_path, [{"vectors": ["change"], "source": "git", "from": "2026-08-01", "until": "2026-09-21"}])
    # Dates in the config are 2026; the fixture rows are T0-based, so use the explicit list through the same path.
    world.gcm.export_grounded_calibration("a", git_root=root, exclusions=WINDOW)

    section = yaml.safe_load((tmp_path / ".breadcrumbs.yaml").read_text())["grounded_calibration"]

    assert section["excluded"][0]["vectors"] == ["change"]
    assert section["excluded"][0]["observations"] == 6


def test_the_block_tells_the_reader_the_gap_excludes_known_bad_rows():
    import importlib.util
    from pathlib import Path

    path = Path(gc.__file__).parents[2] / "plugins" / "claude-code-integration" / "lib" / "calibration_block.py"
    spec = importlib.util.spec_from_file_location("calibration_block_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    block = mod.format_calibration_block(
        {
            "grounded_calibration": {
                "last_updated": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "observations": 10,
                "divergence": {"change": -0.18},
                "excluded": [{"vectors": ["change"], "observations": 284, "reason": "git window spanned the session"}],
            }
        }
    )

    assert "excluded" in block.lower() and "284" in block and "change" in block


# ── pre-release review of 1.14.7 ────────────────────────────────────────────


def test_excluding_every_observation_drops_the_vector_instead_of_reporting_the_prior(world):
    """A replay with nothing left returns the 0.5 prior with zero evidence; emitting that is a fabricated gap."""
    _history(world)
    everything = [{"vectors": ["change"], "source": None, "from": 0.0, "until": None, "reason": "all"}]

    assert "change" not in world.gcm.get_calibration_divergence("a", exclusions=everything)
    assert "change" in world.gcm.get_calibration_divergence("a"), "control: it is there without the exclusion"


def test_an_unquoted_yaml_date_is_read_as_a_date(tmp_path):
    """`from: 2026-08-01` without quotes is a YAML date object, the natural way to write it."""
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(
        "name: x\ncalibration_exclusions:\n  - vectors: [change]\n    source: git\n    from: 2026-08-01\n    until: 2026-09-21\n"
    )

    (entry,) = load_calibration_exclusions(str(tmp_path))

    assert entry["until"] - entry["from"] == 51 * DAY


def test_a_mapping_instead_of_a_list_is_warned_about_not_silently_empty(tmp_path, caplog):
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(
        "calibration_exclusions:\n  vectors: [change]\n  source: git\n"
    )

    with caplog.at_level("WARNING"):
        assert load_calibration_exclusions(str(tmp_path)) == []
    assert "calibration_exclusions" in caplog.text and "list" in caplog.text


def test_an_unknown_vector_name_drops_the_entry_loudly(tmp_path, caplog):
    root = _project(tmp_path, [{"vectors": ["Change"], "source": "git"}, {"vectors": ["change"], "source": ""}])

    with caplog.at_level("WARNING"):
        assert load_calibration_exclusions(root) == []
    assert "Change" in caplog.text


def test_an_entry_that_matches_nothing_is_still_reported_with_zero(world):
    _history(world)
    nothing = [{"vectors": ["change"], "source": "pytest", "from": None, "until": None, "reason": "typo"}]

    assert world.gcm.summarize_exclusions("a", nothing) == [
        {"vectors": ["change"], "observations": 0, "reason": "typo"}
    ]

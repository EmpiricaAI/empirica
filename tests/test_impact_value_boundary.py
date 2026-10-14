"""A non-numeric ``impact`` neither enters the store nor takes bootstrap down (issue #415).

Two legacy findings carried explanatory prose in the numeric ``impact`` column. ``calculate_relevance_score`` guarded
``None`` only, so it multiplied the string and raised, ``project-bootstrap`` died with it, and the PreCompact hook turned
that into a compaction veto. Readers now treat a value they cannot use as the neutral default; writers refuse it, so the
malformed row is never created.
"""

from __future__ import annotations

import time
import uuid

import pytest

from empirica.core.findings_deprecation import FindingsDeprecationEngine
from empirica.data.impact_value import read_impact, require_impact

PROJECT_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())

_PROSE = "high: this unblocks the whole release"


_NOW = time.time()  # decay reads the clock itself, so equal scores are compared to a tolerance, not exactly


def _score(impact):
    return FindingsDeprecationEngine.calculate_relevance_score({"created_timestamp": _NOW, "impact": impact})


@pytest.fixture
def breadcrumbs(tmp_path):
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "impact.db"))
    yield db.breadcrumbs
    db.close()


class TestReader:
    @pytest.mark.parametrize("bad", [_PROSE, "", "n/a", [0.9], {"v": 1}, float("nan"), True])
    def test_a_value_it_cannot_use_scores_like_a_missing_impact(self, bad):
        assert _score(bad) == pytest.approx(_score(None), abs=1e-6)

    def test_the_old_behaviour_was_a_crash(self):
        with pytest.raises(TypeError):
            _ = _PROSE * 1.0

    def test_a_numeric_string_is_read_as_the_number(self):
        assert read_impact("0.9") == 0.9
        assert _score("0.9") == pytest.approx(_score(0.9), abs=1e-6)

    def test_a_real_number_keeps_its_weight(self):
        assert _score(0.9) > _score(0.1)

    def test_reading_clamps_to_the_scale(self):
        assert read_impact(7) == 1.0 and read_impact(-3) == 0.0


class TestWriter:
    @pytest.mark.parametrize("bad", [_PROSE, "high", [0.5], float("nan"), True, float("inf")])
    def test_prose_and_other_non_numbers_are_refused(self, bad):
        with pytest.raises(ValueError, match="impact"):
            require_impact(bad)

    def test_none_stays_none_so_the_caller_can_default(self):
        assert require_impact(None) is None

    def test_numbers_and_numeric_strings_pass_as_floats(self):
        assert require_impact(0.7) == 0.7 and require_impact(1) == 1.0 and require_impact("0.25") == 0.25

    def test_log_finding_refuses_prose_and_stores_nothing(self, breadcrumbs):
        with pytest.raises(ValueError, match="impact"):
            breadcrumbs.log_finding(PROJECT_ID, SESSION_ID, "a finding", impact=_PROSE)
        assert breadcrumbs.get_project_findings(PROJECT_ID) == []

    def test_log_unknown_refuses_prose(self, breadcrumbs):
        with pytest.raises(ValueError, match="impact"):
            breadcrumbs.log_unknown(PROJECT_ID, SESSION_ID, "an unknown", impact=_PROSE)

    def test_log_dead_end_refuses_prose(self, breadcrumbs):
        with pytest.raises(ValueError, match="impact"):
            breadcrumbs.log_dead_end(PROJECT_ID, SESSION_ID, "an approach", "it failed", impact=_PROSE)

    def test_a_numeric_string_is_stored_as_a_number(self, breadcrumbs):
        breadcrumbs.log_finding(PROJECT_ID, SESSION_ID, "string impact", impact="0.8")
        row = breadcrumbs.get_project_findings(PROJECT_ID)[0]
        assert row["impact"] == 0.8 and isinstance(row["impact"], float)

    def test_bootstrap_scoring_survives_a_legacy_row_that_is_already_in_the_store(self, breadcrumbs):
        breadcrumbs.log_finding(PROJECT_ID, SESSION_ID, "a good finding", impact=0.9)
        breadcrumbs.log_finding(PROJECT_ID, SESSION_ID, "the legacy row", impact=0.4)
        breadcrumbs.conn.execute("UPDATE project_findings SET impact = ? WHERE finding = ?", (_PROSE, "the legacy row"))
        breadcrumbs.conn.commit()

        rows = breadcrumbs.get_project_findings(PROJECT_ID)

        assert {r["finding"] for r in rows} == {"a good finding", "the legacy row"}


class TestBatchVerb:
    """log-artifacts used to report a refused node as a bare "Failed to create": the writer's reason was only logged."""

    def test_prose_impact_is_refused_with_its_reason(self):
        from empirica.cli.command_handlers.graph_commands import _impact_refusal

        reason = _impact_refusal({"type": "finding", "ref": "f1", "data": {"finding": "x", "impact": _PROSE}})
        assert reason and "impact" in reason and "number" in reason

    @pytest.mark.parametrize(
        "data", [{"finding": "x"}, {"finding": "x", "impact": 0.7}, {"finding": "x", "impact": "0.7"}]
    )
    def test_a_missing_or_numeric_impact_is_not_refused(self, data):
        from empirica.cli.command_handlers.graph_commands import _impact_refusal

        assert _impact_refusal({"type": "finding", "ref": "f1", "data": data}) is None


class _RaisesOnFloat:
    def __float__(self):
        raise RuntimeError("no")


class TestReaderNeverRaises:
    """The contract is 'never raises'; a JSON-sourced int too large for a float broke it (found in the 1.14.11 review)."""

    @pytest.mark.parametrize("bad", [10**400, 2**1024, _RaisesOnFloat()])
    def test_read_impact_returns_the_default(self, bad):
        assert read_impact(bad) == 0.5

    @pytest.mark.parametrize("bad", [10**400, 2**1024, _RaisesOnFloat()])
    def test_require_impact_refuses_with_a_value_error_the_batch_catches(self, bad):
        with pytest.raises(ValueError, match="impact"):
            require_impact(bad)


class TestRebuildFromNotes:
    """A rebuild must not drop a finding because its stored impact is the legacy prose this fix now refuses on write."""

    def test_a_prose_impact_finding_is_rebuilt_not_dropped(self, breadcrumbs):
        from empirica.cli.command_handlers.sync_commands import _rebuild_insert_breadcrumbs

        class _Db:
            def __init__(self, repo):
                self._repo = repo
                self.adapter = type("A", (), {"conn": repo.conn})()

            def __getattr__(self, name):
                return getattr(self._repo, name)

        items = [
            {"project_id": PROJECT_ID, "session_id": SESSION_ID, "finding": "legacy prose impact", "impact": _PROSE},
            {"project_id": PROJECT_ID, "session_id": SESSION_ID, "finding": "numeric impact", "impact": 0.6},
        ]
        rebuilt = {"findings": 0, "unknowns": 0, "dead_ends": 0, "mistakes": 0}

        _rebuild_insert_breadcrumbs(_Db(breadcrumbs), items, [], [], [], set(), rebuilt)

        assert rebuilt["findings"] == 2, "the legacy row was silently skipped"
        stored = {r["finding"]: r["impact"] for r in breadcrumbs.get_project_findings(PROJECT_ID)}
        assert stored == {"legacy prose impact": 0.5, "numeric impact": 0.6}


class TestBatchOnlyJudgesTypesThatStoreImpact:
    """decision, assumption, mistake and unknown writers never read `impact`; refusing it there broke payloads that worked."""

    @pytest.mark.parametrize("ntype", ["decision", "assumption", "mistake", "unknown", "source"])
    def test_a_stray_impact_on_a_type_that_ignores_it_is_not_refused(self, ntype):
        from empirica.cli.command_handlers.graph_commands import _impact_refusal

        assert _impact_refusal({"type": ntype, "ref": "n", "data": {"impact": _PROSE}}) is None

    @pytest.mark.parametrize("ntype", ["finding", "dead_end"])
    def test_types_that_store_it_still_refuse_prose(self, ntype):
        from empirica.cli.command_handlers.graph_commands import _impact_refusal

        assert _impact_refusal({"type": ntype, "ref": "n", "data": {"impact": _PROSE}})

    def test_an_int_too_large_for_a_float_is_a_refusal_not_a_crash(self):
        from empirica.cli.command_handlers.graph_commands import _impact_refusal

        assert _impact_refusal({"type": "finding", "ref": "n", "data": {"impact": 10**400}})

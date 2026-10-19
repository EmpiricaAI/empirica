"""A rebuild or `project-embed` must put back EVERY finding, not the ones young enough to be "relevant".

Reported by empirica.philipp.empirica-mesh-support (prop_bxjntibm, defect 2). Both refill paths read findings through
``get_project_findings(project_id)``, whose default ``depth="moderate"`` is a RETRIEVAL filter (relevance >= 0.60). Used for a
refill it silently drops everything older than about 21 days at impact 0.5 and 62 days at impact 1.0. Philipp replayed the
filter at the 2026-09-17 rebuild and predicted 576 dropped findings for one practice (measured 579) and 1,225 for another
(measured 1,237). The workspace backfilled 5,046; the next rebuild would have dropped them again.

The test builds a real database and asks the real helper, because a test that re-derives the filter inside itself passes
whether or not the code is right.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from empirica.data.session_database import SessionDatabase

SRC = Path(__file__).resolve().parents[1] / "empirica"
DAY = 86400.0


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    d.conn.execute("PRAGMA foreign_keys=OFF")
    yield d
    d.close()


def _add(db, fid, age_days, impact, *, resolved=0, project="p1"):
    db.conn.execute(
        "INSERT INTO project_findings (id, project_id, session_id, finding, created_timestamp, finding_data, impact, is_resolved) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (fid, project, "s1", f"finding {fid}", time.time() - age_days * DAY, "{}", impact, resolved),
    )
    db.conn.commit()


def test_the_old_call_dropped_what_the_refill_needs(db):
    """Control, so the next test means something: the retrieval default really does drop an old finding."""
    _add(db, "young", 1, 0.5)
    _add(db, "old", 90, 0.5)
    ids = {f["id"] for f in db.get_project_findings("p1")}
    assert ids == {"young"}, "if this stops being true the defect is gone or the fixture is wrong"


def test_the_refill_helper_returns_older_findings_too(db):
    from empirica.core.qdrant.memory import findings_for_reembed

    _add(db, "young", 1, 0.5)
    _add(db, "old", 90, 0.5)
    _add(db, "ancient_low", 400, 0.1)
    _add(db, "other_project", 90, 0.5, project="p2")
    ids = {f["id"] for f in findings_for_reembed(db, "p1")}
    assert ids == {"young", "old", "ancient_low"}, "every finding of THIS project, whatever its age or impact"


def test_resolved_findings_are_refilled_too_because_search_drops_them_at_query_time(db):
    from empirica.core.qdrant.memory import findings_for_reembed

    _add(db, "open_old", 90, 0.5)
    _add(db, "resolved_old", 90, 0.5, resolved=1)
    assert {f["id"] for f in findings_for_reembed(db, "p1")} == {"open_old", "resolved_old"}


def _calls_helper(path: Path) -> bool:
    tree = ast.parse(path.read_text())
    return any(
        isinstance(n, ast.Call) and getattr(n.func, "id", getattr(n.func, "attr", "")) == "findings_for_reembed"
        for n in ast.walk(tree)
    )


@pytest.mark.parametrize("rel", ["core/qdrant/rebuild.py", "cli/command_handlers/project_embed.py"])
def test_both_refill_paths_use_the_helper_and_not_the_retrieval_default(rel):
    path = SRC / rel
    assert _calls_helper(path), f"{rel} must refill through findings_for_reembed"
    tree = ast.parse(path.read_text())
    stray = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "get_project_findings"
    ]
    assert stray == [], f"{rel} still calls get_project_findings directly at {stray}"


# --- write-time visibility ---------------------------------------------------------------------------------------------


def test_a_failed_embed_with_qdrant_available_produces_a_warning_that_names_the_repair():
    from empirica.cli.command_handlers.artifact_log_commands import _embed_warning

    with patch("empirica.core.qdrant.connection._check_qdrant_available", return_value=True):
        msg = _embed_warning(False, "finding")
    assert msg and "not embedded" in msg and "project-embed" in msg


def test_no_warning_when_the_embed_worked():
    from empirica.cli.command_handlers.artifact_log_commands import _embed_warning

    with patch("empirica.core.qdrant.connection._check_qdrant_available", return_value=True):
        assert _embed_warning(True, "finding") is None


def test_no_warning_when_qdrant_is_not_in_use_at_all():
    """A box without Qdrant must not be nagged on every finding: there, 'not embedded' is the configuration."""
    from empirica.cli.command_handlers.artifact_log_commands import _embed_warning

    with patch("empirica.core.qdrant.connection._check_qdrant_available", return_value=False):
        assert _embed_warning(False, "finding") is None

"""Harvested artifacts (graph-harvest, goal 3e9cb368) are marked and never enter a graded window.

A harvest back-fills artifacts from material that already existed: they were not observed in the transaction that
logged them, so calibration must not count them. Every calibration artifact count is scoped to the graded transaction
or session, so the writer stores a harvested artifact with no transaction id, whatever transaction is open.
"""

from empirica.cli.cli_core import create_argument_parser
from empirica.data.epistemic_source import EPISTEMIC_SOURCES, normalize_epistemic_source
from empirica.data.session_database import SessionDatabase


def _db(tmp_path):
    db = SessionDatabase(db_path=str(tmp_path / "s.db"))
    sid = db.create_session(ai_id="harvester")
    return db, sid


def test_harvested_is_a_source_tag():
    assert "harvested" in EPISTEMIC_SOURCES
    assert normalize_epistemic_source("Harvested") == "harvested"


def test_every_writer_drops_the_transaction_for_a_harvested_artifact(tmp_path):
    db, sid = _db(tmp_path)
    p = "proj"
    kw = {"transaction_id": "tx-live", "epistemic_source": "harvested"}
    rows = {
        "project_findings": db.log_finding(p, sid, "harvested finding", **kw),
        "project_unknowns": db.log_unknown(p, sid, "harvested unknown", **kw),
        "project_dead_ends": db.log_dead_end(p, sid, "approach", "why", **kw),
        "mistakes_made": db.log_mistake(sid, "mistake", "why", project_id=p, **kw),
        "assumptions": db.log_assumption(p, sid, "harvested assumption", **kw),
        "decisions": db.log_decision(p, sid, "choice", "rationale", **kw),
    }
    for table, rid in rows.items():
        tx, src = db.conn.execute(
            f"SELECT transaction_id, epistemic_source FROM {table} WHERE id = ?", (rid,)
        ).fetchone()
        assert (tx, src) == (None, "harvested"), table


def test_a_live_artifact_keeps_its_transaction(tmp_path):
    db, sid = _db(tmp_path)
    rid = db.log_finding("proj", sid, "observed now", transaction_id="tx-live", epistemic_source="search")
    assert (
        db.conn.execute("SELECT transaction_id FROM project_findings WHERE id = ?", (rid,)).fetchone()[0] == "tx-live"
    )


def test_the_single_verbs_accept_the_tag():
    parser = create_argument_parser()
    for verb in ("finding-log", "unknown-log", "deadend-log", "assumption-log", "decision-log", "mistake-log"):
        action = next(
            a for a in parser._subparsers._group_actions[0].choices[verb]._actions if a.dest == "epistemic_source"
        )
        assert set(action.choices) == set(EPISTEMIC_SOURCES), verb


def test_retagging_an_artifact_as_harvested_takes_it_out_of_its_transaction():
    from empirica.data.artifact_fields import sibling_updates

    for kind in ("finding", "unknown", "dead_end", "mistake", "assumption", "decision"):
        assert sibling_updates(kind, {"epistemic_source": "harvested"}, 0.0).get("transaction_id", "kept") is None, kind
    assert "transaction_id" not in sibling_updates("finding", {"epistemic_source": "search"}, 0.0)
    both = sibling_updates("decision", {"epistemic_source": "harvested", "outcome": "upheld"}, 5.0)
    assert both == {"transaction_id": None, "outcome_assessed_at": 5.0}

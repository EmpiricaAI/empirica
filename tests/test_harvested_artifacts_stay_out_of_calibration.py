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


def _harvest_rows(db, sid, n=3, resolved_at=None):
    for i in range(n):
        db.log_finding("proj", sid, f"harvested finding {i}", transaction_id="tx-live", epistemic_source="harvested")
        uid = db.log_unknown(
            "proj", sid, f"harvested unknown {i}", transaction_id="tx-live", epistemic_source="harvested"
        )
        if resolved_at is not None:
            db.conn.execute(
                "UPDATE project_unknowns SET is_resolved = 1, resolved_timestamp = ? WHERE id = ?", (resolved_at, uid)
            )
    db.conn.commit()


def test_harvested_unknowns_closed_in_the_window_are_not_backlog_cleared(tmp_path):
    """Sweep (1.17.9): the resolution rate read a NULL transaction as 'inherited', so closing harvested unknowns raised
    do, completion and impact."""
    import time

    from empirica.core.post_test.prose_collector import ProseEvidenceCollector

    db, sid = _db(tmp_path)
    start = time.time() - 60
    _harvest_rows(db, sid, resolved_at=time.time())
    items = ProseEvidenceCollector(sid, project_id="proj", db=db, transaction_id="tx-now", preflight_timestamp=start)
    metrics = {i.metric_name: i for i in items._collect_action_verification()}
    assert "unknown_resolution_rate" not in metrics or metrics["unknown_resolution_rate"].value == 0


def test_harvested_findings_are_not_this_sessions_prose_or_source_ratio(tmp_path):
    from empirica.core.post_test.prose_collector import ProseEvidenceCollector

    db, sid = _db(tmp_path)
    _harvest_rows(db, sid)
    pc = ProseEvidenceCollector(sid, project_id="proj", db=db, transaction_id="tx-now")
    assert not any("harvested finding" in t for t in pc._get_session_texts())
    assert not [i for i in pc._collect_source_quality() if i.metric_name == "source_to_finding_ratio"]


def test_the_time_window_fallback_leaves_harvested_rows_out(tmp_path):
    from empirica.core.post_test.collector import PostTestCollector

    c = PostTestCollector.__new__(PostTestCollector)
    c.transaction_id, c.preflight_timestamp = None, 100.0
    sql, params = c._tx_filter(artifacts=True)
    assert "harvested" in sql and params == (100.0,)
    assert "harvested" not in c._tx_filter()[0]  # reflexes and other tables have no epistemic_source column

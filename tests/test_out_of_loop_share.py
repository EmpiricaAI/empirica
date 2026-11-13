"""The calibration report counts the share of changes made outside a transaction (goal 543fd1c6)."""

from __future__ import annotations

import json

from empirica.core.decision_record import out_of_loop_share


def _rows(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_the_share_counts_changes_in_and_out_of_loop_and_refusals(tmp_path):
    f = tmp_path / "decisions.jsonl"
    _rows(
        f,
        [
            {"ts": 10, "tool": "Edit", "decision": "allow", "rule": "discipline-budget"},  # out of loop
            {"ts": 11, "tool": "Edit", "decision": "allow", "rule": "pipeline"},  # in a transaction
            {"ts": 12, "tool": "Bash", "decision": "allow", "rule": "pipeline", "inferred_effect_classes": ["write"]},
            {"ts": 13, "tool": "Bash", "decision": "allow", "rule": "noetic-firewall", "inferred_effect_classes": []},
            {"ts": 14, "tool": "Bash", "decision": "deny", "rule": "discipline-budget"},  # refused outside
            {"ts": 15, "kind": "outcome", "tool": "Edit"},  # an outcome row, not a decision
            {"ts": 1, "tool": "Write", "decision": "allow", "rule": "discipline-budget"},  # before the window
        ],
    )
    out = out_of_loop_share(f, since=5)
    assert out["changes"] == 3 and out["out_of_loop"] == 1 and out["refused_outside"] == 1
    assert out["share"] == round(1 / 3, 3)
    assert out["by_tool"] == {"edits": {"in": 1, "out": 1}, "commands": {"in": 1, "out": 0}}


def test_no_record_is_said_not_zero(tmp_path):
    out = out_of_loop_share(tmp_path / "missing.jsonl")
    assert out["changes"] == 0 and out["share"] is None and "no decision record" in out["note"]


def test_the_report_reads_the_record_beside_the_practice_database(tmp_path):
    import time
    import types

    from empirica.cli.command_handlers.monitor_commands import _out_of_loop_block

    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    _rows(
        tmp_path / ".empirica" / "decisions.jsonl",
        [{"ts": time.time(), "tool": "Write", "decision": "allow", "rule": "discipline-budget"}],
    )
    db = types.SimpleNamespace(db_path=str(tmp_path / ".empirica" / "sessions" / "sessions.db"))
    out = _out_of_loop_block(db, weeks=1)
    assert out["changes"] == 1 and out["share"] == 1.0 and "discipline budget" in out["definition"]

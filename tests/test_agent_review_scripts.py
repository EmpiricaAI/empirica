"""The agent-review skill's trace builder, anchor checker and stamp schema.

The checker is the part that must not be fooled: its whole value is that a stamped artifact's anchor and quote
are verified against the thread, so each rejection below is a negative control (a forged quote, a missing turn)
set beside the honest stamp that must pass.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import jsonschema
import pytest

SKILL = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/skills/agent-review"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def build_trace():
    return _load("build_trace")


@pytest.fixture(scope="module")
def check_stamp():
    return _load("check_stamp")


def _transcript(tmp_path) -> Path:
    rows = [
        {"message": {"role": "assistant", "content": [{"type": "text", "text": "I will check the loader."}]}},
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "rg -n thresholds core/"}}],
            }
        },
        {
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "content": [
                            {"type": "text", "text": "orchestrator.py:314: data.get('uncertainty_trigger', 0.5)"}
                        ],
                    }
                ],
            }
        },
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Found: the loader reads top-level keys only."}],
            }
        },
    ]
    path = tmp_path / "t.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json at all\n")
    return path


def _stamp(**art):
    base = {
        "type": "finding",
        "text": "the loader reads top-level keys only",
        "anchor": 3,
        "quote": "orchestrator.py:314: data.get('uncertainty_trigger', 0.5)",
        "grounding": "ran",
        "needs_hindsight": False,
    }
    base.update(art)
    return {"thread": "t", "summary": "s", "artifacts": [base], "edges": [], "tasks": [], "proposals": []}


# ---- build_trace --------------------------------------------------------------------------------


def test_the_trace_numbers_every_turn_and_skips_junk_lines(build_trace, tmp_path):
    turns = build_trace.build_turns(_transcript(tmp_path))
    assert [(t["n"], t["role"]) for t in turns] == [
        (1, "assistant"),
        (2, "tool_call"),
        (3, "tool_result"),
        (4, "assistant"),
    ]
    assert turns[1]["text"] == "Bash: rg -n thresholds core/"


def test_a_long_tool_result_is_truncated_to_the_limit(build_trace, tmp_path):
    row = {"message": {"role": "user", "content": [{"type": "tool_result", "content": "x" * 5000}]}}
    path = tmp_path / "long.jsonl"
    path.write_text(json.dumps(row))
    (turn,) = build_trace.build_turns(path, result_limit=100)
    assert len(turn["text"]) == 100


def test_an_unreadable_transcript_is_an_error_not_an_empty_trace(build_trace, tmp_path, capsys):
    path = tmp_path / "empty.jsonl"
    path.write_text("nothing here\n")
    assert build_trace.main([str(path), "--out-dir", str(tmp_path / "out")]) == 1
    assert "no turns found" in capsys.readouterr().err


def test_the_cli_writes_both_files(build_trace, tmp_path):
    out = tmp_path / "out"
    assert build_trace.main([str(_transcript(tmp_path)), "--out-dir", str(out), "--name", "demo"]) == 0
    assert (out / "trace_demo.txt").read_text().startswith("[T1] assistant: I will check the loader.")
    assert len(json.loads((out / "turns_demo.json").read_text())) == 4


# ---- check_stamp --------------------------------------------------------------------------------


def test_an_honest_stamp_passes(build_trace, check_stamp, tmp_path):
    report = check_stamp.check(_stamp(), build_trace.build_turns(_transcript(tmp_path)))
    assert report["ok"] and report["anchors_valid"] == 1 and report["problems"] == []


def test_a_forged_quote_is_rejected(build_trace, check_stamp, tmp_path):
    turns = build_trace.build_turns(_transcript(tmp_path))
    report = check_stamp.check(_stamp(quote="the loader also validates every key"), turns)
    assert not report["ok"] and "verbatim" in report["problems"][0]["reason"]


def test_an_anchor_to_a_turn_that_does_not_exist_is_rejected(build_trace, check_stamp, tmp_path):
    turns = build_trace.build_turns(_transcript(tmp_path))
    report = check_stamp.check(_stamp(anchor=99), turns)
    assert not report["ok"] and "not a turn" in report["problems"][0]["reason"]


def test_a_quote_from_the_wrong_turn_is_rejected(build_trace, check_stamp, tmp_path):
    turns = build_trace.build_turns(_transcript(tmp_path))
    report = check_stamp.check(_stamp(anchor=1), turns)  # the quote lives in T3, not T1
    assert not report["ok"]


def test_whitespace_and_html_entities_do_not_make_an_honest_quote_fail(build_trace, check_stamp, tmp_path):
    turns = build_trace.build_turns(_transcript(tmp_path))
    quote = "orchestrator.py:314:   data.get(&#x27;uncertainty_trigger&#x27;,\n 0.5)"
    assert check_stamp.check(_stamp(quote=quote), turns)["ok"]


def test_an_empty_stamp_is_not_ok(check_stamp):
    assert (
        check_stamp.check({"artifacts": [], "edges": []}, [{"n": 1, "role": "assistant", "text": "x"}])["ok"] is False
    )


def test_an_edge_to_a_missing_artifact_is_reported(build_trace, check_stamp, tmp_path):
    stamp = _stamp()
    stamp["edges"] = [{"from": 0, "to": 7, "relation": "evidence"}]
    report = check_stamp.check(stamp, build_trace.build_turns(_transcript(tmp_path)))
    assert report["edges_with_bad_index"] == [0] and not report["ok"]


def test_the_report_says_how_much_rests_on_the_agents_own_final_report(build_trace, check_stamp, tmp_path):
    turns = build_trace.build_turns(_transcript(tmp_path))
    final = _stamp(anchor=4, quote="the loader reads top-level keys only")
    evidence = _stamp()
    assert check_stamp.check(final, turns)["self_report_share"] == 1.0
    assert check_stamp.check(evidence, turns)["self_report_share"] == 0.0


def test_the_checker_cli_exits_nonzero_on_a_forged_stamp(build_trace, check_stamp, tmp_path):
    turns = build_trace.build_turns(_transcript(tmp_path))
    turns_path, stamp_path = tmp_path / "turns.json", tmp_path / "stamp.json"
    turns_path.write_text(json.dumps(turns))
    stamp_path.write_text(json.dumps(_stamp(quote="invented")))
    assert check_stamp.main([str(stamp_path), str(turns_path)]) == 1


# ---- the schema ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def schema():
    return json.loads((SKILL / "stamp.schema.json").read_text())


def test_the_schema_is_a_valid_json_schema(schema):
    jsonschema.Draft202012Validator.check_schema(schema)


def test_an_honest_stamp_validates(schema):
    jsonschema.validate(_stamp(), schema)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda s: s["artifacts"][0].update(type="opinion"),
        lambda s: s["artifacts"][0].update(grounding="guessed"),
        lambda s: s["artifacts"][0].update(anchor=0),
        lambda s: s["artifacts"][0].update(quote="q" * 201),
        lambda s: s["artifacts"][0].pop("quote"),
        lambda s: s.update(
            proposals=[
                {
                    "objective": "o",
                    "question": "q",
                    "predicted_answer": "a",
                    "reason": "r",
                    "options": [{"label": "only one", "consequence": "c"}],
                }
            ]
        ),
        lambda s: s.update(extra_field=1),
    ],
)
def test_a_malformed_stamp_is_rejected_by_the_schema(schema, mutation):
    stamp = _stamp()
    mutation(stamp)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(stamp, schema)


def test_the_reviewer_prompt_names_only_the_prompt_free_tools():
    prompt = (SKILL / "reviewer-prompt.md").read_text()
    assert "Read, rg, fd, sed -n" in prompt
    assert "{TRACE_PATH}" in prompt and "{ABOUT}" in prompt

"""The transaction skill carries WIRE FORMATS, and a prose trim must not touch them.

Rewritten whole-file for Claude-5 (5790 -> ~2200 words). Most of what went was
duplication: a "Plan Transactions Mode" P1-P5 sequence restating Steps 1-5, two
long worked examples, a rich-markdown tutorial, and an anti-pattern section whose
rules are now single sentences.

What must NOT go is everything a practitioner copies verbatim: the
PREFLIGHT/CHECK/POSTFLIGHT payloads, the 13 vectors, the grounding and verdict
enums, the log-artifacts graph shape, and the compliance response.

This is the file where a whole-file rewrite is most likely to drop a contract
silently, which is exactly why the guard reads the payloads rather than trusting
the word count.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SKILL = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "skills" / "epistemic-transaction" / "SKILL.md"


def _skill() -> str:
    return _SKILL.read_text()


def _heredoc_payloads() -> list[dict]:
    return [json.loads(m.group(1)) for m in re.finditer(r"<< 'EOF'\n(\{.*?\})\nEOF", _skill(), re.S)]


def test_the_guard_can_see_the_payloads():
    """Guards the guard: no payloads parsed means every assertion below is vacuous."""
    assert len(_heredoc_payloads()) >= 4


def test_every_payload_is_valid_json():
    """A practitioner copies these verbatim into a heredoc. A trailing comma or a
    dropped brace fails in their terminal, not here — unless this runs."""
    _heredoc_payloads()  # raises on malformed JSON


def test_the_preflight_payload_carries_all_thirteen_vectors():
    """The 13 vectors are the measurement. A payload missing one teaches an
    incomplete assessment to everyone who copies it."""
    vectors = {
        "know",
        "uncertainty",
        "context",
        "clarity",
        "coherence",
        "signal",
        "density",
        "state",
        "change",
        "completion",
        "impact",
        "do",
        "engagement",
    }
    payloads = [p for p in _heredoc_payloads() if "task_context" in p]
    assert payloads, "the PREFLIGHT example is gone"

    missing = sorted(vectors - set(payloads[0].get("vectors", {})))
    assert not missing, f"PREFLIGHT example lost vectors: {missing}"


def test_the_claims_contract_survives_on_both_sides():
    """CHECK declares {claim, grounding, ref}; POSTFLIGHT adjudicates
    {index|id, verdict, evidence}. Getting these keys wrong is a documented,
    expensive failure — a mis-keyed adjudication was reported back as a
    practitioner discipline gap."""
    payloads = _heredoc_payloads()

    declared = [p for p in payloads if any("grounding" in c for c in p.get("claims", []) if isinstance(c, dict))]
    adjudicated = [p for p in payloads if any("verdict" in c for c in p.get("claims", []) if isinstance(c, dict))]

    assert declared, "the CHECK claims example is gone"
    assert adjudicated, "the POSTFLIGHT adjudication example is gone"

    assert {"claim", "grounding"} <= set(declared[0]["claims"][0])
    assert {"verdict", "evidence"} <= set(adjudicated[0]["claims"][0])


def test_the_enums_survive():
    text = _skill()

    for grounding in ("read", "ran", "retrieved", "assumed"):
        assert f"`{grounding}`" in text, f"grounding '{grounding}' was trimmed"
    for verdict in ("held", "refuted", "untested"):
        assert f"`{verdict}`" in text, f"verdict '{verdict}' was trimmed"
    for work_type in ("remote-ops", "greenfield", "iteration", "investigation", "refactor"):
        assert work_type in text, f"'{work_type}' was trimmed"
    for reversibility in ("exploratory", "committal", "forced"):
        assert reversibility in text, f"reversibility '{reversibility}' was trimmed"


def test_the_log_artifacts_graph_shape_survives():
    """Nodes + edges with a relation vocabulary. Without the edges this degrades to
    a list, which is the failure the graph exists to prevent."""
    graphs = [p for p in _heredoc_payloads() if "nodes" in p]
    assert graphs, "the log-artifacts example is gone"

    g = graphs[0]
    assert g.get("edges"), "edges dropped from the example — the graph becomes a list"
    assert {"from", "to", "relation"} <= set(g["edges"][0])

    text = _skill()
    for relation in ("evidence", "grounded_by", "sourced_from", "invalidates"):
        assert relation in text, f"relation '{relation}' was trimmed"


def test_the_compliance_response_shape_survives():
    text = _skill()

    for key in ("iteration_needed", "max_iterations_exceeded", "check_results", "goal_completion"):
        assert key in text, f"compliance key '{key}' was trimmed"


def test_the_trigger_surface_is_intact():
    """Amendment 3 — front-matter is what makes the skill load; exempt from the trim."""
    m = re.search(r'^description:\s*"(.+)"$', _skill(), re.M)
    assert m, "no description front-matter — this skill can never trigger"

    body = m.group(1).lower()
    for cue in ("plan this as transactions", "break this down", "enterplanmode"):
        assert cue in body, f"trigger cue '{cue}' removed"


def test_the_split_brain_rule_survives_in_some_form():
    """The most common mistake. The anti-pattern SECTION was cut as restatement;
    the rule itself must not go with it."""
    text = _skill().lower()

    assert "split" in text or "separate transactions" in text
    assert "same transaction" in text

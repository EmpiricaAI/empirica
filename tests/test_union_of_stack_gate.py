"""UNION-OF-STACK GATE — the check that per-file review structurally cannot do.

Audit amendment 2, earned from a near-miss: two locally-correct trims can each
defer to the other's file and empty the union. Cortex cut its MESH DISCIPLINE
section because the system prompt carried the steer; the audit's single-home table
simultaneously licensed core to cut that steer as a duplicate. Both cuts were
correct in isolation. Together they would have left three of five mesh obligations
with no always-loaded presence at all — and every per-file review still passed.

So the gate runs over the UNION, not over files: for every obligation whose single
home is LAZY (a skill, loaded on trigger), at least one always-loaded surface must
still carry a steer.

Two rules learned the hard way and applied here:

  * Grep the CONCEPT, not the deleted section's prose. Prose greps cannot
    distinguish "concept removed" from "restatement removed" and will both over-
    and under-count.
  * Use markers distinctive enough to fail. An earlier version of a sibling guard
    matched bare words appearing 7-20 times across the prompt; it PASSED after
    simulating the very cut it guarded. A check that cannot fail is not weak, it is
    false.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "templates" / "empirica-system-prompt-lean.md"

# obligation -> (where it is single-homed, concept markers that must survive
#                in the ALWAYS-LOADED layer)
OBLIGATIONS: dict[str, tuple[str, list[str]]] = {
    "artifact typing": ("constitution §III-b", ["finding", "unknown", "assumption", "mistake"]),
    "graph / edges": ("constitution §III-b", ["edge", "log-artifacts"]),
    "retraction vs staleness": ("constitution §III-b", ["retract"]),
    "transaction lifecycle": ("/epistemic-transaction", ["preflight", "check", "postflight"]),
    "claims + grounding": ("/epistemic-transaction", ["claims", "grounding"]),
    "mesh: pull when uncertain": ("constitution §V", ["collab", "uncertain"]),
    "mesh: ack completions": ("/cortex-mailbox-send", ["ack"]),
    "mesh: don't drop threads": ("/cortex-mailbox-send", ["drop"]),
    "mesh: share sources": ("constitution §V", ["visibility", "shared"]),
    "gardening / resolution": ("/epistemic-gardening", ["resolve", "stale"]),
    "delegation: verify subagents": ("/dispatch-agent", ["subagent", "self-report"]),
    "effort / ceremony scaling": ("§EFFORT AND DELEGATION", ["ceremony"]),
}


def _always_loaded(cortex: bool = True) -> str:
    """Render via the PRODUCTION renderer, not jinja2.

    These tests used `jinja2.Template`. Production does not: `_render_versioned_template`
    substitutes placeholders and strips `{% if cortex %}` blocks with its own string
    handling. So the tests were validating the template against a DIFFERENT ENGINE
    than the one that ships — a passing test proved the template renders under jinja2,
    which nothing in production ever does.

    It also broke CI, because jinja2 is not a declared dependency. The import error
    was the visible symptom; the wrong-engine problem was the real one, and it would
    have survived simply adding jinja2 to the test extras.
    """
    import tempfile
    from pathlib import Path as _P

    from empirica.cli.command_handlers.setup_claude_code import _render_versioned_template

    with tempfile.TemporaryDirectory() as td:
        dst = _P(td) / "out.md"
        _render_versioned_template(_TEMPLATE, dst, cortex_enabled=cortex)
        return dst.read_text().lower()


def test_the_template_renders_so_the_gate_is_not_vacuous():
    """Guards the gate. A render failure would make every check below unreachable
    rather than false — which is how a green suite proves nothing."""
    assert len(_always_loaded().split()) > 1000


@pytest.mark.parametrize("obligation", sorted(OBLIGATIONS))
def test_lazily_homed_obligation_keeps_an_always_loaded_steer(obligation):
    home, markers = OBLIGATIONS[obligation]
    text = _always_loaded()

    missing = [m for m in markers if m not in text]
    assert not missing, (
        f"'{obligation}' is single-homed in {home} (LAZY) and the always-loaded layer "
        f"no longer carries {missing}. A reader who never triggers {home} now never "
        "meets this obligation at all."
    )


def test_the_protected_mesh_bullet_survives():
    """PROACTIVE BEHAVIORS is the designated always-loaded steer for all five mesh
    obligations, and cortex's trim already deferred to it. Cutting it as a
    'duplicate of constitution §V' empties the union — the exact mutual-deference
    gap this gate exists for, which is why the marker comment is in the file."""
    text = _TEMPLATE.read_text()

    assert "DO NOT CUT THE MESH BULLET" in text
    assert "LAZY" in text, "the reason must name the load-tier distinction, not just forbid the cut"

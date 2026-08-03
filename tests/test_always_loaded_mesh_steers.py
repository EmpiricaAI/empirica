"""Obligations single-homed to a LAZY surface must keep an always-loaded steer.

The Claude-5 trim's single-home rule — one concept, one home, delete the
restatements — is right, and it has a blind spot: it does not distinguish
**always-loaded** surfaces from **lazy** ones.

The audit's table homes "mesh discipline framing" in constitution §V and marks
both the system prompt's PROACTIVE BEHAVIORS bullet and cortex-prompt's MESH
DISCIPLINE section as its duplicates. The constitution is a SKILL — loaded on
trigger, absent otherwise. So:

    cortex cut MESH DISCIPLINE      because the system prompt carries it   ✓ locally correct
    core would cut PROACTIVE ...    because the table marks it duplicate   ✓ locally correct
    ------------------------------------------------------------------------
    union                            nobody carries it                     ✗

Three of the five obligations — pull-when-uncertain, push-when-convergent,
don't-drop-threads — would lose their always-loaded presence entirely, and
every per-file review would still pass. It survived four careful reviews.

Found by empirica-mesh-support, who noted the check "does not exist today —
each practice verified its own file". This is that check, as a mechanism rather
than a review step, because a review step is an instruction and an instruction
is not a mechanism.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_TEMPLATE = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "templates"
    / "empirica-system-prompt-lean.md"
)

# DISTINCTIVE phrases, each appearing exactly once in the template — not bare
# intent words.
#
# The first version of this file matched bare words ("pull", "ack", "shared").
# Its failing-first control PASSED after simulating the very cut it guards,
# because those words appear 7, 20 and 8 times across a 5,800-word prompt: the
# assertion could essentially never fail. A check that cannot fail is not a
# weak check, it is a false one — it reports safety it never verified, which is
# exactly the shape this whole trim programme keeps producing.
#
# Phrases are the right granularity: a genuine REWORDING of the steer should
# fail here and be re-approved deliberately, because "did the obligation
# survive rewording" is a judgement call this test must not silently make.
_MESH_OBLIGATIONS = {
    "pull-when-uncertain": "pull when uncertain",
    "push-when-convergent": "push when convergent",
    "ack-what-you-complete": "ack what you complete",
    "dont-drop-threads": "drop threads",
    "register-sources-shared": "register canonical sources",
}


@pytest.fixture(scope="module")
def template_text() -> str:
    return _TEMPLATE.read_text().lower()


@pytest.mark.parametrize(("name", "phrase"), sorted(_MESH_OBLIGATIONS.items()))
def test_every_mesh_obligation_has_an_always_loaded_steer(template_text, name, phrase):
    """The system prompt is always-loaded; the constitution is not.

    If this fails, the obligation now exists ONLY in a lazily-loaded surface —
    a practitioner who never triggers that skill never sees it.
    """
    assert phrase in template_text, (
        f"mesh obligation '{name}' lost its always-loaded steer (phrase: {phrase!r}). "
        "The constitution is a SKILL — homing this there alone means it is absent "
        "unless something triggers the load. Restore a steer here, or move the home "
        "to another always-loaded surface. If you REWORDED it deliberately, update "
        "the phrase here in the same commit."
    )


def test_the_steers_share_one_line_so_a_single_cut_takes_all_five():
    """Why this file exists at all, stated as an assertion.

    All five obligations live on ONE bullet. That is what makes the
    single-home table's "duplicate of constitution §V" verdict so dangerous:
    it licences one deletion that removes five always-loaded steers at once,
    and every per-file review still passes.
    """
    raw = _TEMPLATE.read_text().lower()
    lines = [line for line in raw.splitlines() if "pull when uncertain" in line]

    assert len(lines) == 1
    carrier = lines[0]
    for phrase in _MESH_OBLIGATIONS.values():
        assert phrase in carrier, f"{phrase!r} is not on the shared carrier line — the blast radius changed"


def test_the_template_is_actually_the_always_loaded_one():
    """Guards the test's own premise.

    If setup stopped installing this file, every assertion above would pass
    against a document nobody loads — a green suite proving nothing. The
    installer names this exact path.
    """
    setup = (
        Path(__file__).resolve().parent.parent / "empirica" / "cli" / "command_handlers" / "setup_claude_code.py"
    ).read_text()

    assert "empirica-system-prompt-lean.md" in setup, "this template must be the one setup installs"


def test_the_do_not_cut_marker_survives():
    """The comment is the only thing telling a future trimmer why this looks free.

    Without it the next pass re-derives "duplicate of §V" from the same table
    and makes the same locally-correct cut.
    """
    text = _TEMPLATE.read_text()

    assert "DO NOT CUT THE MESH BULLET" in text
    assert "LAZY" in text, "the reason must name the load-tier distinction, not just forbid the cut"

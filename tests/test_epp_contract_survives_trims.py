"""EPP's skill text carries CONTRACTS, and a prose trim must not cut them.

The Claude-5 subtraction pass rewrote this skill (1718 -> ~940 words). Most of
what went was restatement — the same classify-then-act rule appeared in a step, an
anti-pattern table, and a reference card. But three things in that file are not
prose:

  * five pushback categories — a closed enum on `epp-activate --category`
  * four actions — a closed enum on `epp-activate --action`, and named verbatim
    in the hook's injected pointer
  * the update-threshold table — the judgment core the whole protocol computes on

A future trim reading the file as prose would cut them without any test failing,
and the CLI would keep accepting values the skill no longer teaches. This guard
reads the enums FROM the parser rather than restating them, so the two cannot
drift apart: adding a category to the CLI without documenting it fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SKILL = (
    _ROOT
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "skills"
    / "epistemic-persistence-protocol"
    / "SKILL.md"
)
_HOOK = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "tool-router.py"


def _skill_text() -> str:
    return _SKILL.read_text()


_PARSERS = _ROOT / "empirica" / "cli" / "parsers" / "checkpoint_parsers.py"


def _cli_enum(flag: str) -> set[str]:
    """Read the choices from the parser's DEFINITION SITE, not a copy of them.

    Restating the enum here would just create a third place for it to drift. This
    reads the source so that adding a category to the CLI without documenting it
    in the skill fails this test.
    """
    src = _PARSERS.read_text()
    block = re.search(r"epp_activate_parser\s*=\s*subparsers\.add_parser.*?(?=\n    # )", src, re.S)
    assert block, "the epp-activate parser block moved — this guard needs re-anchoring"

    arg = re.search(rf'"{re.escape(flag)}",.*?choices=\[(.*?)\]', block.group(0), re.S)
    assert arg, f"epp-activate has no {flag} choices — the contract moved"
    return set(re.findall(r'"([^"]+)"', arg.group(1)))


def test_every_pushback_category_is_documented():
    documented = _skill_text().lower()
    missing = sorted(c for c in _cli_enum("--category") if c not in documented)

    assert not missing, (
        f"epp-activate accepts {missing} but the skill no longer teaches them. "
        "A category the CLI takes and the skill omits can never be classified correctly."
    )


def test_every_action_is_documented():
    documented = _skill_text().lower()
    missing = sorted(a for a in _cli_enum("--action") if a not in documented)

    assert not missing, f"epp-activate accepts {missing} but the skill no longer teaches them"


def test_the_update_thresholds_survive():
    """The numbers ARE the protocol — without them 'weigh against your threshold'
    is an instruction with no operand."""
    text = _skill_text()

    for threshold in ("0.85", "0.65", "0.45", "0.25"):
        assert threshold in text, f"update threshold {threshold} was trimmed away"

    for source_type in ("RETRIEVED", "REASONED", "DERIVED", "UNCERTAIN"):
        assert source_type in text, f"source type {source_type} was trimmed away"


def test_the_hook_pointer_and_the_skill_name_the_same_actions():
    """The hook tells Claude to decide HOLD/SOFTEN/UPDATE/REFRAME and then links
    here. If the two ever disagree, the pointer sends Claude to a page that does
    not answer the question the pointer just posed."""
    hook = _HOOK.read_text()
    # Anchored on the closing paren at line start — the pointer text itself
    # contains parens, so a non-greedy match stops inside the string.
    pointer = re.search(r"SEMANTIC_PUSHBACK_POINTER\s*=\s*\(\n(.*?)^\)", hook, re.S | re.M)
    assert pointer, "the injected EPP pointer is gone from the hook"

    skill = _skill_text()
    for action in ("HOLD", "SOFTEN", "UPDATE", "REFRAME"):
        assert action in pointer.group(1), f"hook pointer stopped naming {action}"
        assert action in skill, f"skill stopped naming {action}"


def test_the_skill_still_declares_its_trigger_surface():
    """The front-matter description is what makes a skill LOAD. Trimming it is
    the one cut that silently reduces how often the skill is used at all — the
    same trap as treating the constitution's topic list as a table of contents.
    """
    text = _skill_text()
    desc = re.search(r"^description:\s*>?\s*\n((?:\s{2,}.*\n)+)", text, re.M)
    assert desc, "no description front-matter — this skill can never be triggered"

    body = desc.group(1)
    assert len(body.split()) >= 60, "description trimmed below a usable trigger surface"
    for cue in ("pushback", "sycophantic", "disagree"):
        assert cue in body.lower(), f"trigger cue '{cue}' removed from the description"


def test_no_scripted_response_templates_return():
    """The rewrite's POINT. Four blockquoted first-person scripts mandated
    transcribing internal confidence numbers into user-facing text — the phrasing
    class Anthropic flags for reasoning-echo on Claude-5-generation models, and a
    naturalness cost besides.

    Fails if someone reintroduces a canned reply, which is the natural instinct
    when a protocol feels under-specified.
    """
    text = _skill_text()

    scripts = re.findall(r'^>\s*"', text, re.M)
    assert not scripts, f"{len(scripts)} scripted reply template(s) reintroduced"

    assert "[old]" not in text and "[new]" not in text, (
        "confidence-delta placeholders are back — these mandate narrating internal numbers as response text"
    )

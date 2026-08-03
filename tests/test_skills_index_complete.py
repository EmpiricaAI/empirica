"""Every shipped skill must appear in the skills index.

18 skills shipped with no index and no README mention. You found them by
spotting a `/slash-command` in a prompt, or not at all.

A PARTIAL index is worse than none: it reads as complete, so a skill missing
from it is invisible in a way that looks deliberate. Same failure the docs
coverage ledger exists to catch — a number that only counts what was included
can never surface what was left out.

So the guard runs the other way round: it enumerates what SHIPS and demands
each one appear, rather than trusting the document to describe itself.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "skills"
_INDEX = _ROOT / "docs" / "reference" / "SKILLS.md"


def _shipped_skills() -> set[str]:
    """Directories holding a SKILL.md — the definition of 'ships'.

    Not `os.listdir`: the skills directory has picked up stray non-skill
    directories before (a leftover `.empirica_reflex_logs` from test runs), and
    an index guard that fails on those trains people to ignore it.
    """
    return {d.name for d in _SKILLS_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").exists()}


def test_the_index_exists():
    assert _INDEX.exists(), "the skills index is the entry point; without it skills are undiscoverable"


def test_every_shipped_skill_is_listed():
    listed = set(re.findall(r"`/([a-z0-9-]+)`", _INDEX.read_text()))
    missing = sorted(_shipped_skills() - listed)

    assert not missing, (
        f"skills ship but are absent from docs/reference/SKILLS.md: {missing}. "
        "A partial index reads as complete, so an unlisted skill is invisible."
    )


def test_the_index_does_not_invent_skills():
    """The other direction — a documented skill that does not ship is a phantom.

    Same class as the phantom CLI flags: documented, plausible, unrunnable.
    """
    listed = set(re.findall(r"`/([a-z0-9-]+)`", _INDEX.read_text()))
    shipped = _shipped_skills()
    # Prose references like `/slash-command` are not skill claims; only flag
    # names that look like skills but have no directory.
    phantom = sorted(n for n in listed - shipped if "-" in n and n != "slash-command")

    assert not phantom, f"documented but not shipped: {phantom}"


def test_the_load_bearing_three_are_marked():
    """David's distinction, encoded: these carry most work and must be findable.

    Without it all 18 read as equally weighted, which is what let the trim
    conversation treat them as interchangeable.
    """
    text = _INDEX.read_text()

    for skill in ("epistemic-transaction", "epistemic-gardening", "empirica-constitution"):
        assert skill in text
    assert "load-bearing" in text.lower()


def test_the_readme_points_at_it():
    """An index nobody can find is not an index."""
    readme = (_ROOT / "README.md").read_text()

    assert "docs/reference/SKILLS.md" in readme


def test_every_skill_has_a_trigger_description():
    """A skill with no description never loads — its trigger surface is empty."""
    undescribed = []
    for name in sorted(_shipped_skills()):
        text = (_SKILLS_DIR / name / "SKILL.md").read_text()
        if not re.search(r"^description:\s*\S", text, re.M):
            undescribed.append(name)

    assert not undescribed, f"no description front-matter, so these can never be triggered: {undescribed}"

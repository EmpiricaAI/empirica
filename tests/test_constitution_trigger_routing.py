"""Every advertised load-trigger must route somewhere that answers it.

The system prompt lists ten triggers for `/empirica-constitution`. The Claude-5
audit found the list partly phantom; measured here, **four had zero coverage in
the constitution** — action gating, transaction lifecycle, context management,
reading conversation signals.

The fix was NOT to delete those triggers. Per audit amendment 3, a trigger list is
**trigger surface**: it exists to make the skill load, so cutting entries reduces
how often a load-bearing skill is reached, silently, with the body intact. The fix
is to route each trigger at the surface that actually answers it.

Four of the six mis-routed topics live in the system prompt ITSELF. That is the
expensive kind of wrong: you pay the skill load, the page doesn't answer, and the
honest conclusion is "Empirica doesn't cover this" — about guidance you were
already holding.

This guard is the phantom-pointer detector generalised. It is the same defect
class as a skill printing a CLI flag that does not exist: documented, plausible,
and it fails only in the reader.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_TEMPLATE = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "templates" / "empirica-system-prompt-lean.md"
_CONSTITUTION = (
    _ROOT / "empirica" / "plugins" / "claude-code-integration" / "skills" / "empirica-constitution" / "SKILL.md"
)


def _template() -> str:
    return _TEMPLATE.read_text()


def _routing_rows() -> list[tuple[str, str]]:
    """The `| Topic | Actually lives in |` table under the trigger list."""
    text = _template()
    block = re.search(r"\| Topic \| Actually lives in \|\n\|[-| ]+\|\n((?:\|.*\|\n)+)", text)
    assert block, "the trigger-routing table is gone — triggers are unrouted again"

    rows = []
    for line in block.group(1).strip().splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 2:
            rows.append((cells[0], cells[1]))
    return rows


def test_the_routing_table_exists_and_is_populated():
    rows = _routing_rows()
    assert len(rows) >= 6, f"routing table shrank to {len(rows)} rows — a dropped row is a re-dangled pointer"


def test_every_referenced_section_actually_exists_in_this_file():
    """A row saying "§NOETIC FIREWALL, above" must be true, or it is a phantom
    pointer with extra confidence attached."""
    text = _template()
    headings = {m.group(1).strip().upper() for m in re.finditer(r"^##+\s+(.+)$", text, re.M)}

    missing = []
    for topic, home in _routing_rows():
        for ref in re.findall(r"§([A-Z][A-Z \-,']+)", home):
            name = ref.strip().rstrip(",").upper()
            if not any(name.startswith(h) or h.startswith(name) for h in headings):
                missing.append(f"{topic!r} -> §{name}")

    assert not missing, (
        f"routing rows point at sections that do not exist in the system prompt: {missing}. "
        "Same class as a documented CLI flag that cannot run."
    )


def test_every_referenced_skill_actually_ships():
    skills_dir = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "skills"
    shipped = {d.name for d in skills_dir.iterdir() if d.is_dir() and (d / "SKILL.md").exists()}

    phantom = []
    for topic, home in _routing_rows():
        for slug in re.findall(r"`/([a-z0-9-]+)`", home):
            if slug not in shipped:
                phantom.append(f"{topic!r} -> /{slug}")

    assert not phantom, f"routing rows point at skills that do not ship: {phantom}"


def test_topics_left_to_the_constitution_are_actually_covered_there():
    """The complement of the routing table: whatever the prompt still claims the
    constitution owns must have real coverage in it.

    This is the check that caught the original defect. It reads the sentence the
    prompt uses to make the claim, so the claim and the verification cannot drift.
    """
    text = _template()
    claim = re.search(r"What the constitution genuinely owns:(.+?)\n\n", text, re.S)
    assert claim, "the prompt no longer states what the constitution owns — the claim must stay checkable"

    constitution = _CONSTITUTION.read_text().lower()

    # Topics are DERIVED from the claim sentence, not restated in a fixture. A
    # fixed probe list would silently pass any newly-added topic — which is the
    # same shape as the defect: a claim nobody checks. Adding a topic to that
    # sentence now requires the constitution to actually cover it.
    claimed = re.split(r",| and ", claim.group(1).strip().rstrip("."))

    _STOPWORDS = {"the", "and", "a", "of", "for", "to", "aware", "routing", "system"}
    uncovered = []
    for raw in claimed:
        topic = raw.strip().strip("*` ").lower()
        if not topic:
            continue
        # Stems, so "escalation paths" matches "escalate" and "logging" matches
        # "log". Strict on presence, lenient on phrasing: the failure guarded is
        # ZERO coverage, not imperfect wording.
        stems = [w[:6] for w in re.findall(r"[a-z-]+", topic) if w not in _STOPWORDS and len(w) > 3]
        if stems and not any(s in constitution for s in stems):
            uncovered.append(topic)

    assert not uncovered, (
        f"the prompt sends these to the constitution, which does not cover them: {uncovered}. "
        "Route them at the surface that answers them instead of deleting the trigger — "
        "trigger surface is exempt from subtraction (amendment 3)."
    )


def test_the_trigger_list_itself_is_not_trimmed():
    """Amendment 3: trigger surface is exempt from subtraction.

    A word-count-driven pass will cut this list, because it reads as redundant
    with the constitution's own headings — which is exactly what it is, and
    exactly why it must stay. Deleting entries here does not relocate guidance;
    it reduces how often a load-bearing skill loads at all.
    """
    text = _template()
    triggers = re.search(r"\*\*Load it when you hit any of these\*\*(.+?)\n\n", text, re.S)
    assert triggers, "the trigger list is gone — the constitution now loads only on explicit request"

    listed = triggers.group(1)
    for required in ("artifact logging", "phase-aware completion", "cognitive immune system"):
        assert required in listed, f"trigger '{required}' was trimmed out of the load list"

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


# ─── the circular pointer ─────────────────────────────────────────────────


def test_the_constitution_does_not_route_back_what_the_prompt_routes_to_it():
    """Both documents pointing at each other for the same topic strands the reader.

    The system prompt's trigger list sent 'artifact logging' and 'search routing'
    to the constitution. The constitution's intro sent them BACK to the system
    prompt — while §III-b and §IV substantively cover them. A reader following
    either pointer landed where they started, having passed over the answer.

    Worse than a dangling pointer: a loop terminates only when the reader gives
    up, and giving up looks like "this system doesn't document that".
    """
    prompt = _template()
    claim = re.search(r"What the constitution genuinely owns:(.+?)\n\n", prompt, re.S)
    assert claim, "the prompt must state what it routes to the constitution"

    constitution = _CONSTITUTION.read_text()
    intro = constitution.split("## §I.")[0]

    # Any topic the prompt assigns to the constitution must not appear in a
    # sentence of the intro that routes it back to the system prompt.
    bounce = re.findall(r"^.*load the system prompt.*$", intro, re.M | re.I)
    claimed = claim.group(1).lower()

    for line in bounce:
        for topic in ("artifact logging", "search routing", "escalation"):
            if topic in line.lower() and topic.split()[0] in claimed:
                raise AssertionError(
                    f"circular pointer: the system prompt routes {topic!r} to the constitution, "
                    f"and the constitution routes it back — {line.strip()!r}"
                )


def test_every_section_appears_in_the_constitutions_own_contents():
    """§III-b was real, substantive, and absent from the intro's list — invisible
    in the document's own table of contents while the paragraph below it actively
    disclaimed the topic it covers."""
    constitution = _CONSTITUTION.read_text()
    intro = constitution.split("## §I.")[0]

    sections = re.findall(r"^## (§[IVb\-]+)\.", constitution, re.M)
    missing = [s for s in sections if s not in intro]

    assert not missing, f"sections absent from the constitution's own contents list: {missing}"


# ─── §REPORTING ───────────────────────────────────────────────────────────


def test_the_reporting_section_exists_and_names_all_four_channels():
    """David, 2026-08-03: the monologue/output separation "affects all Empirica
    users", so it belongs in the always-loaded template rather than only in one
    practice's memory.

    The load-bearing claim is the DIAGNOSIS, not the instruction: the verbosity is
    genuine self-awareness that Empirica amplifies, which is why it reads as
    flip-flopping rather than as padding. An instruction to "be concise" without
    that reason gets read as a style preference and ignored under pressure.
    """
    text = _template()

    assert "## REPORTING" in text, "the reporting section is gone"

    section = text.split("## REPORTING", 1)[1].split("\n## ", 1)[0]
    for channel in ("note", "finding-log", "artifact", "user"):
        assert channel in section.lower(), f"channel '{channel}' missing from the routing table"

    assert "flip-flop" in section.lower(), "the diagnosis is what makes this land — keep it"
    assert "compound" in section.lower(), "must say WHY artifacts are the right home"


def test_reporting_has_exactly_one_home():
    """The guidance previously sat buried at the end of CONTEXT IS ABUNDANT — a
    section about context budget, not about what the user reads. Promoted, and the
    old location reduced to a pointer. Two homes would be the duplication defect
    this trim exists to remove.
    """
    text = _template()

    assert text.count("## REPORTING") == 1
    # The old paragraph's distinctive phrasing must not have been left behind.
    assert "Narrating every\nmistake" not in text


def test_concision_is_not_licensed_to_drop_bad_news():
    """A brevity instruction with no floor gets read as licence to omit failures.
    The floor is explicit."""
    section = _template().split("## REPORTING", 1)[1].split("\n## ", 1)[0]

    assert "bad news" in section.lower() or "failures" in section.lower()


def test_compaction_names_resolution_not_only_logging():
    """David: "resolving goals and artifacts is important too, not just logging
    them — epistemic hygiene is paramount."

    Caught a regression: the tightening pass dropped "unresolved goals" from the
    original failure list. The compaction-specific reason is what makes it stick —
    the graph is what survives compaction and what gets retrieved BACK, so a stale
    or unresolved entry does not sit inertly, it returns as though current and
    mis-steers the next decision.
    """
    section = _template().split("## COMPACTION", 1)[1].split("\n## ", 1)[0]

    assert "unresolved" in section.lower(), "the failure list must include unresolved work, not just unlogged"
    assert "retract" in section.lower(), "retraction is the move practices reliably skip — name it"
    for concept in ("resolv", "graph"):
        assert concept in section.lower(), f"'{concept}' missing — the reason is what makes the rule stick"


# ─── cortex-gated content ─────────────────────────────────────────────────


def _render(cortex: bool) -> str:
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


def test_the_template_renders_with_and_without_cortex():
    """Guards the guard: a template that fails to render would make every
    assertion below unreachable rather than false."""
    assert len(_render(True).split()) > 1000
    assert len(_render(False).split()) > 1000


def test_cortex_only_surfaces_do_not_leak_into_cortex_less_installs():
    """A cortex-less install must not be pointed at layers it does not have.

    `empirica-org-prompt.md` is owned and distributed by CORTEX, not core — core
    ships only `empirica-system-prompt-lean.md`. The alias paragraph referencing
    the org-prompt layer sat OUTSIDE the `{% if cortex %}` guard, so every
    cortex-less install read a pointer to a file that never existed for it.

    Same class as the constitution's dangling triggers, one layer out: a reference
    that resolves for the author and for nobody else.
    """
    plain = _render(False)

    for cortex_only in ("empirica-org-prompt.md", "cortex_collab", "target_claudes"):
        assert cortex_only not in plain, (
            f"'{cortex_only}' leaks into a cortex-less render — it points at something that install does not have"
        )


def test_the_canonical_addressing_contract_survives_on_cortex_installs():
    """The 3-form is a WIRE contract — bare basenames bounce. It must not be
    trimmed along with the alias prose around it."""
    withc = _render(True)

    assert "<org>.<tenant>.<exact-project-name>" in withc
    assert "delivery_failed" in withc


# ─── no canned vector values in any skill ─────────────────────────────────


def test_no_skill_prescribes_canned_vector_values():
    """Three skills printed a fixed vector block for the practitioner to submit
    verbatim — `know: 0.2, uncertainty: 0.7` and so on — as step one of their
    procedure.

    That teaches the single habit the measurement layer exists to prevent:
    reporting numbers you did not assess. A pasted vector set is not a LOW reading,
    it is a FABRICATED one, and it corrupts the calibration record more than
    skipping the transaction would. It is also the plausible mechanism behind the
    measured 47%-of-728 rubber-stamp CHECK rate.

    The payload SHAPE is a contract and stays. The numbers are the practitioner's.
    `/epistemic-transaction` is exempt: it documents the payload format itself, and
    a format example needs illustrative values.
    """
    import re
    from pathlib import Path

    skills = Path(_ROOT) / "empirica" / "plugins" / "claude-code-integration" / "skills"
    offenders = []
    for d in sorted(skills.iterdir()):
        if not (d / "SKILL.md").exists() or d.name == "epistemic-transaction":
            continue
        text = (d / "SKILL.md").read_text()
        # A concrete float assigned to a vector key inside a submitted payload.
        if re.search(r'"(know|uncertainty|clarity|coherence|engagement)":\s*0\.\d', text):
            offenders.append(d.name)

    assert not offenders, (
        f"these skills prescribe canned vector values: {offenders}. "
        "Print the payload shape; let the practitioner supply their own assessment."
    )


def test_effort_and_delegation_guidance_exists():
    """The audit's item 6: the stack was near-silent on both, and both are things
    Claude-5-generation models do differently.

    Before this there was ONE mention of subagents in the whole always-loaded
    layer, and it was a definition. Nothing mapped task size to transaction weight,
    so the default drifts to full ceremony for everything — and a PREFLIGHT whose
    reasoning is thinner than the task deserves is a rubber-stamp CHECK one phase
    earlier.
    """
    section = _template().split("## EFFORT AND DELEGATION", 1)
    assert len(section) == 2, "the effort/delegation section is gone"
    body = section[1].split("\n## ", 1)[0].lower()

    # Ceremony scaling must name the no-CHECK route — it is the one practitioners
    # skip, and skipping it correctly is the point.
    assert "no check" in body

    # Delegation must carry the verify rule, not just permission to delegate.
    assert "self-report" in body or "verify" in body
    assert "fork" in body, "fork-vs-fresh is the routing decision, not an aside"
    assert "does not persist" in body, "an unlogged subagent discovery is lost — say so"


def test_effort_guidance_does_not_restate_the_harness():
    """Anthropic's harness already carries act-when-ready, brevity and autonomy.
    Duplicating them here is the same repetition defect one layer up — against the
    harness instead of against another file in this stack.
    """
    body = _template().split("## EFFORT AND DELEGATION", 1)[1].split("\n## ", 1)[0].lower()

    for harness_owned in ("be concise", "act when ready", "lead with the outcome"):
        assert harness_owned not in body, f"'{harness_owned}' is the harness's job, not ours"

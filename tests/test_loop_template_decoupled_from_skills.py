"""ser_c96ac4a3 strip-phase: loop installs must survive skill removal.

`canonical_loops.py` declares `body_skill: cortex-mailbox-poll`, and the
installer substitutes that skill's `## Cron Prompt Template`. The strip phase
removes the mesh skills from the open plugin — which would have silently
degraded every loop install to the generic placeholder, because the template
lived only inside the skill being deleted. The template is machine-consumed
loop shell, not proprietary teaching content, so it now ships in the package
(`core/cockpit/loop_templates/`), checked FIRST.
"""

from __future__ import annotations

from pathlib import Path

from empirica.core.cockpit.loop_install_request import _extract_skill_prompt_template

REPO = Path(__file__).resolve().parent.parent


def test_packaged_template_exists_for_every_declared_body_skill_with_a_template():
    """The invariant that makes skill removal safe."""
    assert (REPO / "empirica/core/cockpit/loop_templates/cortex-mailbox-poll.md").is_file()


def test_packaged_template_is_the_first_candidate_and_parses():
    """The honest version of "survives the strip".

    A HOME-monkeypatch cannot simulate the strip: the repo skill dir is derived
    from __file__ and keeps resolving until the skills are actually deleted —
    the negative control caught the first version of this test passing with the
    fix stashed. What IS provable now: the packaged file parses to a real
    template via the same reader the installer uses, and it sits FIRST in the
    candidate order, so when the skill dirs go, resolution cannot change.
    """
    import inspect

    from empirica.core.cockpit import loop_install_request as lir

    template = lir._template_from(Path(lir.__file__).resolve().parent / "loop_templates" / "cortex-mailbox-poll.md")
    assert template, "packaged template does not parse via the installer's own reader"

    src = inspect.getsource(lir._extract_skill_prompt_template)
    # Order within the CANDIDATES LIST — the docstring also mentions the paths,
    # so anchor the search past `candidates = [`.
    cand = src[src.index("candidates = [") :]
    assert cand.index("loop_templates") < cand.index("skills_runtime / body_skill"), (
        "packaged candidate is not checked first"
    )


def test_packaged_template_matches_the_skill_copy_while_both_exist():
    """Until the skill is deleted, the two copies must not drift — the packaged
    one is canonical from now on, and a divergent skill copy would mean edits
    landing in the doomed location."""
    packaged = (REPO / "empirica/core/cockpit/loop_templates/cortex-mailbox-poll.md").read_text()
    skill = (
        REPO / "empirica/plugins/claude-code-integration/skills/cortex-mailbox-poll/references/cron-prompt-template.md"
    )
    if skill.exists():  # after the strip lands this copy is gone, and that is fine
        assert packaged == skill.read_text(), "packaged and skill templates have drifted"


def test_missing_template_still_returns_none():
    """message-cleanup has never carried a template; the packaged-first lookup
    must not invent one."""
    assert _extract_skill_prompt_template("no-such-skill-ever") is None

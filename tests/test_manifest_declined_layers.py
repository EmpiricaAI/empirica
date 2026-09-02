"""A declaration gate needs three states, not two — and the third needs a reason.

Every manifest in the fleet already carried its deliberate refusals as YAML comments:

    prompts:
      - empirica-system-prompt.md
      # empirica-crm-prompt.md — deliberately NOT consumed. Capability-scoped …

To a parser a considered refusal and a practice that simply forgot are the same
bytes: nothing. So a gate reporting "has the capability, no declaration" fires on the
most carefully-declared seat in the fleet — and **a check that cries wolf gets
silenced the first time it does.**

Two practices independently invented a `declined:` mapping within an hour of each
other, each in a position `extra="forbid"` rejects. Independent reinvention of a
shape the schema lacks is the signal.

The tests that matter here assert the REASON and the READER, not the field: a
declined name without a reason says no more than the silence it replaces, and a field
with no reader is what let this drift persist unnoticed in the first place.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from empirica.core.modules.manifest import (
    CONSUMES,
    DECLINED,
    UNDECLARED,
    ManifestError,
    declaration_state,
    load_manifest,
    validate_manifest_file,
)

BASE = """\
empirica_module:
  name: probe
  seat_name: probe
  version: "0.1.0"
  requires:
{requires}
"""


def _write(tmp_path: Path, requires: str) -> Path:
    p = tmp_path / "module.yaml"
    p.write_text(BASE.format(requires=textwrap.indent(textwrap.dedent(requires), "    ")))
    return p


CONSUMED_AND_DECLINED = """\
prompts:
  - empirica-system-prompt.md
declined:
  prompts:
    empirica-crm-prompt.md: "capability-scoped to workspace CRUD seats"
"""


# ── the three states ─────────────────────────────────────────────────────────


def test_the_three_states_are_distinguishable(tmp_path):
    """THE regression. Before this, the second and third collapsed into one."""
    m = load_manifest(_write(tmp_path, CONSUMED_AND_DECLINED))

    assert declaration_state(m, "empirica-system-prompt.md") == (CONSUMES, None)
    assert declaration_state(m, "empirica-crm-prompt.md") == (
        DECLINED,
        "capability-scoped to workspace CRUD seats",
    )
    assert declaration_state(m, "empirica-org-prompt.md") == (UNDECLARED, None)


def test_skills_get_the_same_third_state(tmp_path):
    """`prompts` and `skills` are both list[str] and both gate-able; giving only one
    of them the third state would leave the same defect one field along."""
    m = load_manifest(
        _write(
            tmp_path,
            """\
            skills:
              - cortex-mailbox-poll
            declined:
              skills:
                eat-the-broccoli: "release-gate skill; this seat cuts no releases"
            """,
        )
    )

    assert declaration_state(m, "cortex-mailbox-poll", kind="skills")[0] == CONSUMES
    assert declaration_state(m, "eat-the-broccoli", kind="skills")[0] == DECLINED
    assert declaration_state(m, "code-audit", kind="skills")[0] == UNDECLARED


# ── the reason is the whole point ────────────────────────────────────────────


@pytest.mark.parametrize("reason", ['""', '"   "'], ids=["empty", "whitespace"])
def test_a_declined_layer_without_a_reason_is_rejected(tmp_path, reason):
    """A bare declined name reproduces the original silence one level up: you would
    know a layer was refused and not why, so a gate could report the fact and nothing
    a reader could act on."""
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, f"declined:\n  prompts:\n    x.md: {reason}\n"))

    assert "reason" in str(exc.value)
    assert "x.md" in str(exc.value), "and it must name WHICH entry"


def test_a_layer_cannot_be_both_consumed_and_declined(tmp_path):
    """A contradiction that validates is worse than one that does not, because the
    gate would answer whichever branch it happened to check first."""
    with pytest.raises(ManifestError) as exc:
        load_manifest(
            _write(
                tmp_path,
                """\
                prompts:
                  - dup.md
                declined:
                  prompts:
                    dup.md: "a reason"
                """,
            )
        )

    assert "dup.md" in str(exc.value)


# ── it must not break what already ships ─────────────────────────────────────


def test_a_manifest_with_no_declined_block_still_validates(tmp_path):
    """NEGATIVE CONTROL. Four practices ship manifests today; a required new block
    would have invalidated every one of them at the next core upgrade."""
    m = load_manifest(_write(tmp_path, "prompts:\n  - a.md\n"))

    assert m.requires.declined.prompts == {}
    assert declaration_state(m, "b.md") == (UNDECLARED, None)


def test_the_old_wrong_shape_still_fails_loudly(tmp_path):
    """POSITIVE CONTROL on `extra='forbid'`. `declined` nested INSIDE the prompts list
    is what both practices first reached for; widening the schema must not have
    quietly started accepting it somewhere else."""
    with pytest.raises(ManifestError):
        load_manifest(_write(tmp_path, "prompts:\n  consumes: [a.md]\n"))


# ── the reader, and the surface that renders it ──────────────────────────────


def test_the_validate_receipt_renders_all_three(tmp_path):
    """A declared refusal no surface ever shows is a comment with extra steps. The
    receipt is what `module-validate` prints, so it has to carry the reasons."""
    receipt = validate_manifest_file(_write(tmp_path, CONSUMED_AND_DECLINED))

    assert receipt["ok"] is True
    declarations = receipt["declarations"]["prompts"]
    assert declarations[CONSUMES] == ["empirica-system-prompt.md"]
    assert declarations[DECLINED]["empirica-crm-prompt.md"].startswith("capability-scoped")


def test_declaration_state_refuses_an_unknown_kind(tmp_path):
    """The gate consumers pass `kind` through from their own config. A typo must not
    silently resolve to UNDECLARED for every layer — that is a gate reporting the
    whole fleet as undeclared and being believed."""
    m = load_manifest(_write(tmp_path, "prompts:\n  - a.md\n"))

    with pytest.raises(ValueError, match="kind"):
        declaration_state(m, "a.md", kind="promtps")

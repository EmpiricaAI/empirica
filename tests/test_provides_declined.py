"""`provides` needs the third state `requires` got — and its wrong answer costs more.

`requires.declined` answers *what does this practice not consume*. Getting it wrong
produces a missing file, which fails at first use.

`provides.declined` answers *what is this practice not FOR*. Getting it wrong produces
a **misroute**, which fails silently for as long as the sender is patient, because the
work lands somewhere that looks like it is handling it.

Measured instance: a prEN 18229-1 standards-submission deadline was escalated to a
practice whose entire scope is remote-ops — correctly addressed, entirely misrouted —
and sat for a day looking coordinated. The exclusion existed the whole time, as a YAML
comment, because `provides` set `extra="forbid"` and had no exclusion field.

Second instance of a gap this schema had already been shown to have and fixed on one
block only.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from empirica.core.modules.manifest import (
    CONSUMES,
    DECLINED,
    PROVIDES,
    UNDECLARED,
    ManifestError,
    declaration_state,
    load_manifest,
)

BASE = """\
empirica_module:
  name: probe
  seat_name: probe
  version: "0.1.0"
{body}
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "module.yaml"
    p.write_text(BASE.format(body=textwrap.indent(textwrap.dedent(body), "  ")))
    return p


SCOPED = """\
provides:
  domains: [remote-ops, provisioning]
  declined:
    domains:
      governance: "not a governance practice — escalations here sit looking coordinated"
"""


def test_a_practice_can_say_what_it_is_NOT_for(tmp_path):
    """THE regression. `not_for` / any exclusion key was rejected outright, so the
    scope statement could only be a comment."""
    m = load_manifest(_write(tmp_path, SCOPED))

    assert declaration_state(m, "governance", kind="domains") == (
        DECLINED,
        "not a governance practice — escalations here sit looking coordinated",
    )


def test_the_positive_state_is_named_for_its_block(tmp_path):
    """A domain a practice OFFERS is `provides`, not `consumes`. One word answering
    two questions is the collapse this field exists to remove — shipping it inside the
    reader would have been perverse."""
    m = load_manifest(_write(tmp_path, SCOPED))

    assert declaration_state(m, "remote-ops", kind="domains")[0] == PROVIDES
    assert declaration_state(m, "provisioning", kind="domains")[0] == PROVIDES


def test_requires_keeps_its_own_word(tmp_path):
    """POSITIVE CONTROL on the split. Naming the provides state must not rename the
    requires one — gates already read `consumes` from 8bb714d12."""
    m = load_manifest(_write(tmp_path, "requires:\n  prompts: [a.md]\n"))

    assert declaration_state(m, "a.md")[0] == CONSUMES


def test_an_unmentioned_domain_is_undeclared(tmp_path):
    """The whole point of three states: silence is still distinguishable from both a
    claim and a refusal."""
    m = load_manifest(_write(tmp_path, SCOPED))

    assert declaration_state(m, "client-delivery", kind="domains") == (UNDECLARED, None)


def test_a_declined_domain_needs_a_reason(tmp_path):
    """Same rule as requires.declined, and for the same reason: a bare name relocates
    the silence rather than removing it. Enforced by a shared base so the two blocks
    cannot drift apart on it."""
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'provides:\n  declined:\n    domains:\n      gov: ""\n'))

    assert "reason" in str(exc.value)
    assert "gov" in str(exc.value), "and it must name WHICH entry"


def test_a_domain_cannot_be_both_provided_and_declined(tmp_path):
    """A contradiction that validates is worse than one that does not — a router would
    answer whichever branch it checked first."""
    with pytest.raises(ManifestError) as exc:
        load_manifest(_write(tmp_path, 'provides:\n  domains: [ops]\n  declined:\n    domains:\n      ops: "reason"\n'))

    assert "ops" in str(exc.value)


def test_skills_exist_on_BOTH_blocks_and_do_not_collide(tmp_path):
    """`skills` means *consumed* under requires and *offered* under provides. Without
    the `side` discriminator one word would answer two questions — exactly the defect
    the declined field was added to fix."""
    m = load_manifest(
        _write(
            tmp_path,
            """\
            requires:
              skills: [cortex-mailbox-poll]
            provides:
              skills: [eat-the-broccoli]
            """,
        )
    )

    assert declaration_state(m, "cortex-mailbox-poll", kind="skills", side="requires")[0] == CONSUMES
    assert declaration_state(m, "eat-the-broccoli", kind="skills", side="provides")[0] == PROVIDES
    assert declaration_state(m, "eat-the-broccoli", kind="skills", side="requires")[0] == UNDECLARED


def test_an_unknown_axis_raises(tmp_path):
    """A typo must not resolve to UNDECLARED for everything — that is a router
    reporting the whole fleet as unscoped and being believed."""
    m = load_manifest(_write(tmp_path, SCOPED))

    with pytest.raises(ValueError, match="kind"):
        declaration_state(m, "x", kind="domians")


def test_existing_manifests_are_unaffected(tmp_path):
    """NEGATIVE CONTROL. `declined` defaults to empty on both blocks; a manifest that
    validated before must still validate, or the fix invalidates the fleet."""
    m = load_manifest(_write(tmp_path, "provides:\n  domains: [ops]\n"))

    assert m.provides.declined.domains == {}
    assert declaration_state(m, "anything", kind="domains") == (UNDECLARED, None)

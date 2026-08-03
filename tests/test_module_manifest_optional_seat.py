"""Two blockers reported by empirica-cortex, found by RUNNING the validator.

1. `extra="forbid"` is set on every model, so `requires_runtime.mcp`,
   `requires.skills` and `requires.prompts` were not "missing keys a practice
   could add" — they were keys the schema actively refused. A core model change
   has to land before any practice can author them.

2. `seat` was required with no default, and cortex has no seat doc. That is
   structural rather than an oversight: cortex's role body ships through
   ecosystem-update's prompts component, so a `seat.import` in its repo would
   either point at a file it does not hold or duplicate content homed
   elsewhere — the two-competing-canonicals problem. Requiring the block forced
   a choice between fabricating a seat doc to satisfy a validator and having no
   manifest at all.

`extra="forbid"` STAYS. It is what produced a precise "Extra inputs are not
permitted" naming the exact key, rather than silently dropping it — the whole
reason this was diagnosable.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from empirica.core.modules.manifest import ModuleManifest

_MINIMAL = {"name": "m", "seat_name": "empirica.david.m", "version": "1.0"}


def test_a_practice_with_no_seat_layer_can_author_a_manifest():
    """Cortex is the proof that manifest-having and seat-having are different sets."""
    m = ModuleManifest(**_MINIMAL)

    assert m.seat is None


def test_seat_name_is_still_required():
    """The BLOCK is optional; the IDENTITY is not.

    Every practice has a canonical id — executors.py keys join_practice_domain
    off `seat_name` — even when it has no seat LAYER to install. Making the
    whole thing optional would have thrown away the identity with the flags.
    """
    with pytest.raises(ValidationError) as exc:
        ModuleManifest(name="m", version="1.0")

    assert "seat_name" in str(exc.value)


def test_a_seat_block_still_validates_when_present():
    m = ModuleManifest(**_MINIMAL, seat={"import": "docs/SEAT.md", "mode": "dedicated"})

    assert m.seat is not None
    assert m.seat.import_ == "docs/SEAT.md"
    assert m.seat.mode == "dedicated"


def test_runtime_mcp_dependencies_are_declarable():
    m = ModuleManifest(**_MINIMAL, requires_runtime={"mcp": ["cortex", "empirica"]})

    assert m.requires_runtime.mcp == ["cortex", "empirica"]


def test_skill_and_prompt_dependencies_are_declarable():
    m = ModuleManifest(**_MINIMAL, requires={"skills": ["eat-the-broccoli"], "prompts": ["org"]})

    assert m.requires.skills == ["eat-the-broccoli"]
    assert m.requires.prompts == ["org"]


def test_all_three_default_to_empty_not_none():
    """A module declaring nothing must iterate cleanly, not crash on None."""
    m = ModuleManifest(**_MINIMAL)

    assert m.requires_runtime.mcp == []
    assert m.requires.skills == []
    assert m.requires.prompts == []


@pytest.mark.parametrize(
    ("block", "bad_key"),
    [("requires_runtime", "mcps"), ("requires", "skill"), ("provides", "prompt")],
)
def test_typos_are_still_refused_loudly(block, bad_key):
    """extra=forbid is the reason this was diagnosable — it must not be relaxed.

    A schema that accepted unknown keys would have silently ignored
    `requires_runtime.mcp` instead of naming it, and the report would have been
    "my module does not get its MCP server" with no pointer to why.
    """
    with pytest.raises(ValidationError) as exc:
        ModuleManifest(**_MINIMAL, **{block: {bad_key: ["x"]}})

    assert "Extra inputs are not permitted" in str(exc.value)
    assert bad_key in str(exc.value)


# --- provides must be able to answer what requires asks ----------------------


def test_a_practice_can_declare_hosting_an_mcp_server():
    """The supply side of `requires_runtime.mcp`, which shipped without one.

    Cortex's capability surface is a hosted MCP server plus a REST API — none
    of skills/agents/hooks/automations. `provides: {}` was accurate and useless.
    """
    m = ModuleManifest(**_MINIMAL, provides={"mcp": ["cortex"], "services": ["cortex-rest"]})

    assert m.provides.mcp == ["cortex"]
    assert m.provides.services == ["cortex-rest"]


def test_every_required_axis_has_a_matching_provided_axis():
    """A dependency declarable in one direction only is half a graph.

    Every seat requires the cortex MCP server; before this, nothing could
    declare providing it, so a requires→provides drift check would report an
    unsatisfiable dependency forever. Asserted structurally so adding a new
    `requires_runtime` axis without its supply side fails here.
    """
    from empirica.core.modules.manifest import Provides, RequiresRuntime

    required = set(RequiresRuntime.model_fields)
    provided = set(Provides.model_fields)

    assert "mcp" in required and "mcp" in provided, "mcp must be declarable on both sides"


def test_a_requires_provides_pair_resolves():
    """The end-to-end property mesh-support's drift check needs."""
    consumer = ModuleManifest(**_MINIMAL, requires_runtime={"mcp": ["cortex"]})
    provider = ModuleManifest(
        name="cortex", seat_name="empirica.david.empirica-cortex", version="1.0", provides={"mcp": ["cortex"]}
    )

    unsatisfied = set(consumer.requires_runtime.mcp) - set(provider.provides.mcp)

    assert unsatisfied == set(), "the consumer's requirement must be satisfiable by the provider's declaration"


def test_provides_defaults_stay_empty_lists():
    m = ModuleManifest(**_MINIMAL)

    assert m.provides.mcp == []
    assert m.provides.services == []

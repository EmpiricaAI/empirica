"""One artifact-type registry. A private copy is the defect, not the divergence.

`empirica delete-artifacts` with `{"type":"lesson"}` answered *Unknown artifact
type* — for a type the codebase's own canonical registry names on purpose.
Reported by mesh-support, measured on installed 1.13.27. Three copies existed:

| where | types | missing |
|---|---|---|
| `artifact_fields.ARTIFACT_TABLES` (canonical) | 8 | — |
| `graph_commands._ARTIFACT_TABLES` (private) | 7 | `source` |
| `profile_commands.table_map` (private) | 5 | `assumption`, `decision`, `source` |

`update-artifacts` had been migrated to the registry and `delete-artifacts`, **in
the same file** as the seven-entry copy, had not.

**One of those three divergences was deliberate, and unifying blindly would have
widened a destructive verb.** `source` was left out of the delete map ON PURPOSE:
sources are archived, not deleted. But the same map also answered *what exists?*
for edge validation — so omitting `source` made `_artifact_exists` return False
for every source id, `prune_dangling` judged every `sourced_from` edge dangling,
and a routine gardening pass destroyed a practice's only two citation edges while
both endpoints sat on disk.

That is one predicate answering two questions that agree everywhere except on
things that are **archived**. So: unify the TABLES, keep the POLICY explicit.
`DELETABLE_TYPES` is now a named exclusion with a reason attached, rather than an
omission that reads as a typo.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from empirica.data.artifact_fields import (
    ARTIFACT_EDGE_DATA_COLUMNS,
    ARTIFACT_TABLES,
    DELETABLE_TYPES,
    FOREIGN_STORE_TYPES,
    NON_DELETABLE_REASON,
    artifact_table,
)

ROOT = Path(__file__).resolve().parent.parent / "empirica"

#: Every module that used to keep its own copy, plus the canonical home.
CONSUMERS = (
    ROOT / "cli" / "command_handlers" / "graph_commands.py",
    ROOT / "cli" / "command_handlers" / "profile_commands.py",
)


# ── the registry is coherent with itself ─────────────────────────────────────


def test_every_table_has_an_edge_column_entry():
    """Parity, so adding a type cannot silently skip edge cleanup."""
    assert set(ARTIFACT_TABLES) == set(ARTIFACT_EDGE_DATA_COLUMNS)


def test_source_is_in_the_tables_and_out_of_the_deletable_set():
    """THE distinction. Present for existence checks, absent for destruction."""
    assert "source" in ARTIFACT_TABLES, "an archived source still EXISTS — edges point at it"
    assert "source" not in DELETABLE_TYPES, "sources are archived, never deleted"
    assert "source" in NON_DELETABLE_REASON, "and the refusal must name the alternative"
    assert "source-archive" in NON_DELETABLE_REASON["source"]


def test_every_non_deletable_type_carries_a_reason():
    """A refusal without a reason is indistinguishable from an unknown type —
    which is exactly how the old omission read."""
    for atype in set(ARTIFACT_TABLES) - DELETABLE_TYPES:
        assert atype in NON_DELETABLE_REASON, f"{atype} is refused with no stated reason"


def test_lesson_resolves_to_no_local_table_but_is_a_declared_type():
    """The reported symptom: `lesson` is declared, just stored elsewhere."""
    assert "lesson" in FOREIGN_STORE_TYPES
    assert artifact_table("lesson") is None
    assert "lesson" not in ARTIFACT_TABLES


@pytest.mark.parametrize("atype", sorted(ARTIFACT_TABLES))
def test_every_declared_type_resolves(atype):
    resolved = artifact_table(atype)
    assert resolved is not None
    table, id_col, _edge = resolved
    assert table and id_col


def test_an_unknown_type_resolves_to_none():
    """NEGATIVE CONTROL — without this, a resolver returning a default would pass
    every case above while resolving garbage too."""
    assert artifact_table("not-a-type") is None


# ── no module keeps a private copy ───────────────────────────────────────────


#: The real table names. A private map is recognised by pointing AT one of these.
KNOWN_TABLES = {table for table, _id in ARTIFACT_TABLES.values()}


def _module_level_type_maps(path: Path) -> list[str]:
    """Dict literals mapping artifact types to TABLE NAMES, at any scope.

    Keyed on CONTENT rather than on variable name — the three copies were called
    `_ARTIFACT_TABLES` and `table_map`, so a name-based probe finds one of them.

    But keys alone are too broad, and the first version of this proved it by
    flagging `NODE_REQUIRED_FIELDS` and a help-text schema: both legitimately map
    artifact types to something that is not a table. A guard that fires on correct
    code gets disabled rather than obeyed, so the discriminator is the VALUE — a
    dict is a private table map only if it points at a table this registry names.
    """
    tree = ast.parse(path.read_text())
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
        if len(keys & set(ARTIFACT_TABLES)) < 3:
            continue
        # Does any value name a real table? Handles both shapes the copies used:
        # a bare string, and a tuple whose first element is the table.
        named = set()
        for value in node.values:
            for sub in ast.walk(value):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and sub.value in KNOWN_TABLES:
                    named.add(sub.value)
        if named:
            offenders.append(f"{path.name}:{node.lineno} tables={sorted(named)}")
    return offenders


@pytest.mark.parametrize("path", CONSUMERS, ids=lambda p: p.name)
def test_no_consumer_keeps_a_private_artifact_type_map(path):
    """The rule, enforced structurally. David's read: a knowledge graph should
    already know its types."""
    assert not _module_level_type_maps(path), (
        f"private artifact-type map in {path.name} — import from empirica.data.artifact_fields instead"
    )


def test_the_probe_would_have_caught_all_three_originals(tmp_path):
    """NEGATIVE CONTROL, with the real pre-fix shapes.

    Both original names are included because the probe keys on content: a
    name-based check would have found `_ARTIFACT_TABLES` and missed `table_map`.
    """
    pre_fix = tmp_path / "pre_fix.py"
    pre_fix.write_text(
        "_ARTIFACT_TABLES = {\n"
        "    'finding': ('project_findings', 'id', 'finding_data'),\n"
        "    'unknown': ('project_unknowns', 'id', 'unknown_data'),\n"
        "    'goal': ('goals', 'id', 'goal_data'),\n"
        "}\n"
        "def f():\n"
        "    table_map = {'finding': 'project_findings', 'unknown': 'project_unknowns', 'goal': 'goals'}\n"
        "    return table_map\n"
    )
    assert len(_module_level_type_maps(pre_fix)) == 2, "both the module-level and the in-function copy"


def test_the_probe_does_not_fire_on_an_ordinary_dict(tmp_path):
    """A guard that fires on unrelated literals gets disabled rather than obeyed."""
    benign = tmp_path / "benign.py"
    benign.write_text("OPTS = {'output': 'json', 'limit': 20}\nPAIR = {'finding': 1, 'goal': 2}\n")
    assert _module_level_type_maps(benign) == [], "two overlapping keys is below the threshold"


def test_the_probe_does_not_fire_on_a_per_type_dict_that_is_not_a_table_map(tmp_path):
    """NEGATIVE CONTROL earned the hard way — the first version flagged
    `NODE_REQUIRED_FIELDS` and a help-text schema, both correct code."""
    fields = tmp_path / "fields.py"
    fields.write_text(
        "NODE_REQUIRED_FIELDS = {\n"
        "    'finding': ['finding'],\n"
        "    'unknown': ['unknown'],\n"
        "    'mistake': ['mistake', 'why_wrong'],\n"
        "}\n"
    )
    assert _module_level_type_maps(fields) == [], "per-type FIELDS are not a table map"

"""Reference docs must not document functions that do not exist.

`docs/reference/api/` documents 102 function-shaped symbols. 43 of them do not
exist anywhere in the codebase — with full signatures, parameter tables, and
runnable examples that fail on the first line:

    from empirica.core.tasks.repository import TaskRepository
    task_repo.create_task(goal_id=..., description=..., priority="high")

`TaskRepository` is real. `create_task` is not; the method is
`save_subtask(subtask: SubTask)`. Three of these files are marked
**Stability: Stable**.

This is worse than missing documentation. Missing docs send you to the code;
confidently wrong reference docs send you to a signature that was never there,
and the failure surfaces at runtime in the reader's project rather than in ours.

Editing 43 entries by hand fixes today and drifts again by the next refactor,
because nothing would notice. So the durable half is this test: any function
documented in `docs/reference/api/` must exist, and the known-stale set below is
a frozen inventory — it can only shrink. Adding a new phantom fails CI; fixing
one and removing it from the list is the intended direction of travel.

The inventory is deliberately explicit rather than a count. A number would let
someone swap one phantom for another and stay green.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

API_DOCS = Path(__file__).parent.parent / "docs" / "reference" / "api"

# EMPTY, and it stays empty. The 43 phantom entries this list was created to
# track were removed on 2026-08-01 and replaced with a generated-from-source
# surface. An entry here would mean a documented function does not exist —
# which is now a CI failure, not a debt to record.
KNOWN_STALE: dict[str, set[str]] = {}

_HEADING = re.compile(r"^#+ `?([a-z_]\w*)\(", re.M)


def _defined_symbols() -> set[str]:
    """Every function and method name defined in the shipped packages.

    Matches methods as well as module-level functions, which is what the docs
    mostly describe — `create_task(self, ...)` is documented as a method and
    would be found here if it existed.
    """
    files = subprocess.run(
        ["bash", "-c", "find empirica empirica-mcp -name '*.py' 2>/dev/null"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    ).stdout.split()
    src = "\n".join(Path(Path(__file__).parent.parent / f).read_text(encoding="utf-8", errors="ignore") for f in files)
    return set(re.findall(r"^\s*(?:async )?def ([A-Za-z_]\w*)", src, re.M))


@pytest.fixture(scope="module")
def defined() -> set[str]:
    return _defined_symbols()


def _documented(doc: Path) -> list[str]:
    return list(dict.fromkeys(_HEADING.findall(doc.read_text(encoding="utf-8"))))


@pytest.mark.parametrize("doc", sorted(API_DOCS.glob("*.md")), ids=lambda p: p.name)
def test_documented_functions_exist(doc: Path, defined: set[str]):
    """POSITIVE CONTROL for new drift: a newly-documented phantom fails here."""
    missing = {n for n in _documented(doc) if n not in defined}
    unexpected = missing - KNOWN_STALE.get(doc.name, set())

    assert not unexpected, (
        f"{doc.name} documents {sorted(unexpected)}, which do not exist in the codebase.\n"
        "Either the symbol was renamed/removed (fix the doc) or it is not built yet "
        "(do not document it as reference API)."
    )


@pytest.mark.parametrize("doc_name", sorted(KNOWN_STALE))
def test_the_stale_inventory_only_shrinks(doc_name: str, defined: set[str]):
    """The other direction, and the one that makes this worth having.

    If a symbol on the known-stale list starts existing, the list is out of date
    and must be trimmed — otherwise the inventory silently becomes a permanent
    exemption instead of a debt being paid down.
    """
    now_real = {n for n in KNOWN_STALE[doc_name] if n in defined}

    assert not now_real, (
        f"{doc_name}: {sorted(now_real)} now exist — remove them from KNOWN_STALE. "
        "The inventory is a debt list, not an allowlist."
    )


def test_every_stale_entry_is_actually_documented():
    """Guards the guard: an entry naming a symbol the doc no longer mentions is
    dead weight that makes the debt look larger than it is."""
    orphaned = {}
    for name, stale in KNOWN_STALE.items():
        doc = API_DOCS / name
        if not doc.exists():
            orphaned[name] = "file no longer exists"
            continue
        gone = stale - set(_documented(doc))
        if gone:
            orphaned[name] = sorted(gone)

    assert not orphaned, f"KNOWN_STALE names symbols no longer documented: {orphaned}"


def test_the_inventory_is_not_growing_silently():
    """A single number someone would have to consciously raise. Swapping one
    phantom for another keeps the per-file tests green; this does not."""
    assert sum(len(v) for v in KNOWN_STALE.values()) == 0

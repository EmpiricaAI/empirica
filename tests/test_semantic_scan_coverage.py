"""Every tracked doc is indexed, or semantic search silently cannot find it.

A peer practice reported their `SEMANTIC_INDEX.yaml` reading
`total_docs_indexed: 1` against 43 real files — "not broken, never regenerated,
and it FAILS SILENTLY: an index covering almost nothing still answers queries,
just with almost nothing."

Ours was healthy in that direction (497 entries, 0 pointing at missing files)
and broken in the other: SCAN_RULES named `docs/architecture`, `docs/reference`,
`docs/guides`, `docs/human` and `docs/*.md`, so a doc in ANY other subdirectory
matched no rule and was invisible. Measured 2026-08-02: `docs/examples/` and
`docs/foundation/` uncovered, and MESSAGING_LAYERS.md and UPGRADE_TO_1.13.md
were unreachable by semantic search the day they were written.

The fix is a catch-all `docs/**/*.md` placed last (first match wins, so the typed
rules still win). A rule per directory would fix today and re-break the next time
someone adds a folder — coverage-by-default makes omission impossible rather than
merely remembered.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO = Path(__file__).parent.parent


def _tracked_docs() -> set[str]:
    out = subprocess.run(["git", "ls-files", "docs"], cwd=REPO, capture_output=True, text=True).stdout
    return {p for p in out.split() if p.endswith(".md")}


def test_every_tracked_doc_matches_a_scan_rule():
    """POSITIVE CONTROL — the reproduction. A doc no rule matches is invisible
    to semantic search with no error and no staleness signal."""
    from empirica.core.docs.semantic_scan import SCAN_RULES

    covered = set()
    for rule in SCAN_RULES:
        covered |= {str(p.relative_to(REPO)) for p in REPO.glob(rule.glob)}

    uncovered = sorted(_tracked_docs() - covered)

    assert not uncovered, (
        f"{len(uncovered)} tracked docs match no SCAN_RULE and are unreachable by semantic search: {uncovered[:5]}"
    )


def _first_matching_rule(root: Path, relpath: str):
    """Which rule wins for a path, using the SAME mechanism the scanner uses.

    `Path.match` is NOT glob: it matches from the right and treats `**`
    differently, so `Path("docs/architecture/X.md").match("docs/architecture/**/*.md")`
    is False while `root.glob(...)` matches it. An earlier version of these tests
    used `Path.match` and both a pass and a failure were meaningless.
    """
    from empirica.core.docs.semantic_scan import SCAN_RULES

    for rule in SCAN_RULES:
        if relpath in {str(p.relative_to(root)) for p in root.glob(rule.glob)}:
            return rule
    return None


def test_a_doc_in_a_brand_new_subdirectory_is_covered(tmp_path):
    """The structural claim. Adding a folder must not require adding a rule —
    that is the failure mode the catch-all exists to remove. Built in a temp
    tree so it tests the rule set, not this repo's current layout."""
    d = tmp_path / "docs" / "some-folder-nobody-has-created-yet"
    d.mkdir(parents=True)
    (d / "NOTES.md").write_text("x")

    rule = _first_matching_rule(tmp_path, "docs/some-folder-nobody-has-created-yet/NOTES.md")

    assert rule is not None, "a doc in an unnamed subdirectory matches no rule — coverage is not default"


def test_the_typed_rules_still_win_over_the_catch_all(tmp_path):
    """NEGATIVE CONTROL: first match wins, so the catch-all must sit AFTER the
    typed rules. If it shadowed them every doc would lose its doc_type and the
    index would stop distinguishing architecture from guides."""
    d = tmp_path / "docs" / "architecture"
    d.mkdir(parents=True)
    (d / "X.md").write_text("x")

    rule = _first_matching_rule(tmp_path, "docs/architecture/X.md")

    assert rule is not None and rule.doc_type == "architecture", (
        f"the catch-all is shadowing typed rules — docs/architecture resolved to {rule.doc_type if rule else None!r}"
    )

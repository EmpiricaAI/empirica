"""docs-explain discarded every semantic hit, then reported keyword mode.

`--question "How do I start a session?"` answered with "## Category Index".
Semantic search was in fact returning exactly the right documents —
`01_START_HERE.md`, `04_QUICKSTART_CLI.md` — and every one of them was thrown
away one line later.

The two sides key the same document differently. Embedding stores the
repo-relative path (`docs/human/end-users/X.md`); the in-memory dict is keyed
relative to the DOCS DIR (`human/end-users/X.md`). So `doc_path in docs` was
never true, `scored_docs` stayed empty, and the code fell through to its keyword
fallback — reporting `Search: keyword`, which was honest about the mode and
silent about the fact that the primary had produced good hits and lost them.

Semantic mode had therefore never once been used, on any query, by anyone.

Same shape as the six other wrong-key lookups fixed in this release: a lookup
keyed on a value that usually looks like the right one.
"""

from __future__ import annotations

from empirica.cli.command_handlers.docs_commands import DocsExplainAgent

DOCS = {
    "human/end-users/01_START_HERE.md": "# Create a session",
    "architecture/TRIGGER_MODEL.md": "# Triggers",
}


def test_the_embedded_prefix_is_stripped_to_match():
    """POSITIVE CONTROL — the exact reproduction."""
    assert DocsExplainAgent._match_doc_key("docs/human/end-users/01_START_HERE.md", DOCS) == (
        "human/end-users/01_START_HERE.md"
    )


def test_an_exact_key_still_matches():
    """NEGATIVE CONTROL: the already-correct case must not regress."""
    assert DocsExplainAgent._match_doc_key("architecture/TRIGGER_MODEL.md", DOCS) == "architecture/TRIGGER_MODEL.md"


def test_a_deeper_prefix_also_resolves():
    """Segments are stripped progressively, so the match survives either side
    changing its base directory later."""
    assert DocsExplainAgent._match_doc_key("repo/docs/human/end-users/01_START_HERE.md", DOCS) == (
        "human/end-users/01_START_HERE.md"
    )


def test_the_inverse_direction_resolves():
    """Dict keyed WITH a prefix the embedding lacks — the same bug mirrored."""
    docs = {"docs/architecture/TRIGGER_MODEL.md": "# Triggers"}

    assert DocsExplainAgent._match_doc_key("architecture/TRIGGER_MODEL.md", docs) == (
        "docs/architecture/TRIGGER_MODEL.md"
    )


def test_an_unrelated_path_does_not_match():
    """NEGATIVE CONTROL: matching by suffix must not become matching anything.
    Returning a wrong document would be worse than the fallback it replaced."""
    assert DocsExplainAgent._match_doc_key("some/other/UNRELATED.md", DOCS) is None


def test_a_missing_path_is_not_an_error():
    assert DocsExplainAgent._match_doc_key(None, DOCS) is None
    assert DocsExplainAgent._match_doc_key("", DOCS) is None

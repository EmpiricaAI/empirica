"""The docs index skips docs/_archive (goal 95c3ee16).

docs/_archive is gitignored and exists only on the machine that archived a doc, so indexing it put files other machines
cannot open into the tracked SEMANTIC_INDEX.yaml.
"""

from empirica.core.docs.semantic_scan import _should_skip


def test_archived_docs_are_skipped():
    assert _should_skip("docs/_archive/architecture/AI_WORKFLOW_AUTOMATION.md")
    assert _should_skip("docs/_archive/guides/UPGRADE_TO_1.9.md")


def test_live_docs_are_not():
    assert not _should_skip("docs/architecture/SYNC_ARCHITECTURE.md")

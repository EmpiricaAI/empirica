"""A key absent from the payload must not pass the guard and then be indexed.

Reported by a peer practice as `docs-explain error: 'audience'` — a bare key name
where an answer should have been.

    if result.get("audience") != "all":
        print(f"👤 Audience: {result['audience']}")

The guard is safe (`.get`); the use is not (`[]`). An ABSENT audience returns
None, `None != "all"` is True, control ENTERS the branch, and it then KeyErrors
on the very key whose absence let it in.

**The missing key passes the guard because it is missing.** That inversion is
what makes it survive review: the guard looks defensive, and the failure only
appears for payloads that omit the field entirely — which is exactly what
generate_semantic_index.py writes (keys: concepts, description, doc_type, tags —
no audience).
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.docs_commands import _print_explain_human_output


@pytest.mark.parametrize(
    ("payload", "expect_line"),
    [
        ({"ok": True, "query": "q", "search_mode": "keyword"}, False),  # absent — the bug
        ({"ok": True, "query": "q", "search_mode": "keyword", "audience": "all"}, False),
        ({"ok": True, "query": "q", "search_mode": "keyword", "audience": "developer"}, True),
        ({"ok": True, "query": "q", "search_mode": "keyword", "audience": "user"}, True),
        # The AI-first default. Now emitted on EVERY index entry, so rendering
        # it would put a content-free line under every answer.
        ({"ok": True, "query": "q", "search_mode": "keyword", "audience": "ai"}, False),
        ({"ok": True, "query": "q", "search_mode": "keyword", "audience": None}, False),
        ({"ok": True, "query": "q", "search_mode": "keyword", "audience": ""}, False),
    ],
)
def test_audience_rendering_never_raises_on_a_missing_key(payload, expect_line, capsys):
    """POSITIVE CONTROL is the first case: an absent audience used to raise."""
    _print_explain_human_output(payload)

    out = capsys.readouterr().out
    assert ("👤 Audience:" in out) is expect_line


def test_the_index_emits_an_audience_so_the_guard_is_not_vacuous():
    """A guard on a key nothing ever writes is decoration.

    The peer who reported the KeyError made the sharper point: fixing the
    render leaves `audience` absent from every entry the scanner produces, so
    the branch simply never runs. The field has to exist at the source.
    """
    from empirica.core.docs.semantic_scan import classify_audience

    assert classify_audience("docs/human/end-users/GUIDE.md") == "user"
    assert classify_audience("docs/human/developers/API.md") == "developer"
    assert classify_audience("docs/architecture/TRIGGER_MODEL.md") == "ai"
    assert classify_audience("docs\\human\\end-users\\GUIDE.md") == "user", "windows separators"


def test_the_cli_classifier_does_not_carry_its_own_copy_of_the_rule():
    """Two hand-maintained copies of a classifier drift. One definition."""
    import inspect

    from empirica.cli.command_handlers.docs_commands import EpistemicDocsAgent

    body = inspect.getsource(EpistemicDocsAgent._classify_audience)
    assert "classify_audience" in body, "must delegate"
    assert "human/end-users" not in body, "must not re-implement the rule"

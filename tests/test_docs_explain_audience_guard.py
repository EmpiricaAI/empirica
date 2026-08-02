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
        ({"ok": True, "query": "q", "search_mode": "keyword", "audience": None}, False),
        ({"ok": True, "query": "q", "search_mode": "keyword", "audience": ""}, False),
    ],
)
def test_audience_rendering_never_raises_on_a_missing_key(payload, expect_line, capsys):
    """POSITIVE CONTROL is the first case: an absent audience used to raise."""
    _print_explain_human_output(payload)

    out = capsys.readouterr().out
    assert ("👤 Audience:" in out) is expect_line

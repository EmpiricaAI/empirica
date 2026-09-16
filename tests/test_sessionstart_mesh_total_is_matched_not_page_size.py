"""The SessionStart mesh block must state how many exist, not how many it received.

The block renders `## 📬 Pending mesh messages (N) — handle these FIRST`. That
sentence is an instruction to treat the set as the session's opening obligation,
so the number governs behaviour: work the page to zero and the block agrees you
are done.

`N` was `len(proposals)` — the size of the returned page. The CLI puts `matched`
(how many exist), `has_more`, and a `truncated_hint` in the very object the hook
parses, and the hook dropped all three.

Measured before the fix:

  * this practice's own session announced **20**; the true backlog was **28**
    (archived 20, re-polled, found 8 more)
  * a peer measured **20** against `matched=118` — blind to 83% of its own inbox

The producer was already correct and had been fixed from an earlier incident of
this exact shape. One consumer discarded the fields. That is why a producer-side
fix cannot reach this, and why reading the CLI would have cleared it.

These tests drive the real function with a stubbed `subprocess.run`, so they
exercise the parse-and-render path a session actually gets.
"""

from __future__ import annotations

import importlib.util
import json
import re
import types
from pathlib import Path

import pytest

_HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "ewm-protocol-loader.py"
)


@pytest.fixture(scope="module")
def hook():
    """Load the hook by path — its filename is not an importable module name."""
    spec = importlib.util.spec_from_file_location("ewm_protocol_loader", _HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _payload(*, returned: int, matched: int | None, has_more: bool) -> str:
    props = [
        {
            "id": f"prop_{i:026d}",
            "source_claude": "empirica.david.empirica-peer",
            "status": "accepted",
            "type": "collab_brief",
            "title": f"title {i}",
            "summary": f"summary {i}",
        }
        for i in range(returned)
    ]
    body: dict = {"ok": True, "proposals": props}
    if matched is not None:
        body["matched"] = matched
        body["has_more"] = has_more
    return json.dumps(body)


def _render(hook, monkeypatch, stdout: str) -> str:
    # The hook does `import subprocess` INSIDE the function, so there is no
    # module attribute to patch — the name is resolved from the real module at
    # call time. Patch it there.
    import subprocess

    monkeypatch.setattr(hook, "_resolve_ai_id_for_poll", lambda: "empirica", raising=False)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: types.SimpleNamespace(returncode=0, stdout=stdout, stderr=""),
    )
    return hook._build_pending_inbox_lead()


def _headline_number(block: str) -> int:
    m = re.search(r"Pending mesh messages \((\d+)\)", block)
    assert m, f"no headline count in: {block[:200]!r}"
    return int(m.group(1))


def test_the_headline_states_matched_not_the_page_size(hook, monkeypatch):
    """The defect, as an assertion: 20 returned, 118 exist."""
    block = _render(hook, monkeypatch, _payload(returned=20, matched=118, has_more=True))

    assert _headline_number(block) == 118, "the headline reported a page size as the total"


def test_this_practices_own_specimen(hook, monkeypatch):
    """The exact numbers this session produced, kept as a regression case."""
    block = _render(hook, monkeypatch, _payload(returned=20, matched=28, has_more=True))

    assert _headline_number(block) == 28
    assert "…and 20 more" in block, "the overflow line must count from the true total, not the page"


def test_a_truncated_poll_does_not_advertise_a_command_that_reproduces_it(hook, monkeypatch):
    """ "Run this for the full list" must actually produce the full list.

    The bare command re-runs the same default limit and returns the same partial
    page — the instruction under-delivering in precisely the way the count did.
    """
    block = _render(hook, monkeypatch, _payload(returned=20, matched=118, has_more=True))

    assert "--limit 118" in block, "a truncated poll must tell the reader how to get the rest"


def test_an_untruncated_poll_keeps_the_simple_command(hook, monkeypatch):
    """The positive control for the line above.

    Without it, appending --limit unconditionally would pass the truncation test
    while adding noise to every ordinary session.
    """
    block = _render(hook, monkeypatch, _payload(returned=12, matched=12, has_more=False))

    assert _headline_number(block) == 12
    assert "--limit" not in block
    assert "…and 4 more" in block


def test_an_older_cortex_without_matched_still_renders(hook, monkeypatch):
    """`matched` is omitted entirely by an older cortex.

    Falling back to the page size is the honest degraded behaviour — the same
    number as before the fix, which is correct when nothing better is on offer.
    A KeyError here would blank the mesh block for every seat on an old server.
    """
    block = _render(hook, monkeypatch, _payload(returned=5, matched=None, has_more=False))

    assert _headline_number(block) == 5


def test_a_matched_smaller_than_the_page_is_not_trusted(hook, monkeypatch):
    """Defensive: a smaller `matched` than the rows in hand is incoherent.

    Rendering it would claim fewer pending messages than the block goes on to
    list — visibly absurd, and the sort of thing that trains readers to distrust
    the number. Fall back to what we can see.
    """
    block = _render(hook, monkeypatch, _payload(returned=9, matched=3, has_more=False))

    assert _headline_number(block) == 9


def test_the_block_is_empty_when_nothing_pends(hook, monkeypatch):
    """Guards the suite's premise: these assertions must be reachable.

    If the block returned "" for every input, every test above would need to fail
    rather than silently pass on an empty string.
    """
    assert _render(hook, monkeypatch, _payload(returned=0, matched=0, has_more=False)) == ""

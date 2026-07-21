"""project-embed incremental skip — re-embed only new/changed findings.

Regression for ecodex prop_hel7dlbw: project-embed re-embedded EVERY eidetic
finding on every call (the md5 content_hash fed only the point_id, never a skip
check), so a repeat embed was O(all) and blew past the session-end embed budget.
`_filter_unembedded` now drops findings already stored with an identical
content_hash; `_auto_embed_project` runs it detached (fire-and-forget).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from empirica.cli.command_handlers.project_embed import _filter_unembedded


def _hash(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def _pid(fact_id: str) -> int:
    return int(hashlib.md5(fact_id.encode()).hexdigest()[:15], 16)


class _Point:
    def __init__(self, id: int, content_hash: str):
        self.id = id
        self.payload = {"content_hash": content_hash}


class _FakeClient:
    """Minimal Qdrant stand-in: retrieve returns seeded points keyed by id."""

    def __init__(self, stored: dict | None = None, raise_on_retrieve: bool = False):
        self._stored = stored or {}  # {point_id: content_hash}
        self._raise = raise_on_retrieve

    def retrieve(self, collection_name, ids, with_payload=None, with_vectors=None):
        if self._raise:
            raise RuntimeError("collection missing")
        return [_Point(i, self._stored[i]) for i in ids if i in self._stored]


def _finding(fid: str, text: str) -> dict:
    return {"id": fid, "finding": text}


def _valid(*findings):
    # mirrors _rehydrate_eidetic's `valid` tuple: (finding, text, content_hash)
    return [(f, f["finding"], _hash(f["finding"])) for f in findings]


def test_all_unchanged_skips_everything():
    f1, f2 = _finding("a", "alpha"), _finding("b", "beta")
    stored = {_pid("a"): _hash("alpha"), _pid("b"): _hash("beta")}
    assert _filter_unembedded(_FakeClient(stored), "coll", _valid(f1, f2)) == []


def test_changed_content_is_reembedded():
    f1, f2 = _finding("a", "alpha"), _finding("b", "beta-v2")
    stored = {_pid("a"): _hash("alpha"), _pid("b"): _hash("beta-OLD")}
    todo = _filter_unembedded(_FakeClient(stored), "coll", _valid(f1, f2))
    assert [t[0]["id"] for t in todo] == ["b"]


def test_new_finding_is_embedded():
    f1, f2 = _finding("a", "alpha"), _finding("c", "gamma")
    stored = {_pid("a"): _hash("alpha")}  # only 'a' stored
    todo = _filter_unembedded(_FakeClient(stored), "coll", _valid(f1, f2))
    assert [t[0]["id"] for t in todo] == ["c"]


def test_missing_collection_embeds_all():
    f1, f2 = _finding("a", "alpha"), _finding("b", "beta")
    todo = _filter_unembedded(_FakeClient(raise_on_retrieve=True), "coll", _valid(f1, f2))
    assert len(todo) == 2  # retrieve failed → embed everything (first-run safety)


def test_tuple_shape_carries_point_id():
    todo = _filter_unembedded(_FakeClient({}), "coll", _valid(_finding("a", "alpha")))
    _f, _text, content_hash, point_id = todo[0]
    assert content_hash == _hash("alpha")
    assert point_id == _pid("a")


def test_session_end_embed_is_fire_and_forget():
    """Part B guard: the session-end auto-embed must be detached, not a blocking
    subprocess.run with a timeout (which the 3s SessionEnd cap kills mid-run)."""
    src = (
        Path(__file__).parent.parent.parent
        / "empirica"
        / "plugins"
        / "claude-code-integration"
        / "hooks"
        / "session-end-postflight.py"
    ).read_text()
    # Isolate the _auto_embed_project body.
    body = src.split("def _auto_embed_project", 1)[1].split("\ndef ", 1)[0]
    assert "start_new_session=True" in body, "auto-embed must be detached"
    assert "subprocess.run(" not in body, "auto-embed must not block on subprocess.run"
    assert "this is incremental and fast" not in body, "stale false comment must be gone"

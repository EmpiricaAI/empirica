"""The type defined to cross the practice boundary was the one type that never crossed.

Measured 2026-08-21 against the live pool:

    global_learnings   954 points   type: finding 954, lesson 0
    empirica_lessons    32 points   project_id: None on all 32

The vocabulary says a **finding describes local state** and a **lesson transfers a
pattern across the practice boundary**. The sync moved findings and not lessons,
so `project-search --global` could surface a peer's local description and never
their transferable pattern. `sharing_policy` — authored, indexed, four values —
was consulted by nothing: a lesson published `public` propagated exactly as far
as one marked `private`.

Three properties this pins, each of which was false before:

1. **The gate is the AUTHORED policy, not impact.** Impact is the right question
   about an observation; sharing is a decision the practitioner already made.
2. **Superseded lessons do not propagate.** Federating a retired lesson is worse
   than not federating it — it arrives at a peer with no local supersession edge
   to suppress it, so the one practice that knows it was replaced is the only one
   that stops serving it.
3. **The return is a breakdown, not a count.** `{"synced": 0}` alone cannot tell
   "nothing was eligible" from "Qdrant is down" from "every write failed", and
   those need different responses.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core.qdrant import global_sync as gs

SCHEMA = """
CREATE TABLE lessons (
    id TEXT PRIMARY KEY, name TEXT, description TEXT, domain TEXT,
    sharing_policy TEXT, abstraction_level TEXT
);
"""


class _Store:
    def __init__(self, conn, retired=()):
        self._conn = conn
        self._retired = dict.fromkeys(retired, "newer")

    def superseded_ids(self):
        return self._retired


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A lesson store and a capturing embedder, with Qdrant reported available."""
    conn = sqlite3.connect(tmp_path / "l.db")
    conn.executescript(SCHEMA)
    embedded: list[dict] = []

    def _fake_embed(*, item_id, text, item_type, project_id, tags=None, **_):
        embedded.append({"item_id": item_id, "text": text, "type": item_type, "project_id": project_id, "tags": tags})
        return True

    monkeypatch.setattr(gs, "_check_qdrant_available", lambda: True)
    monkeypatch.setattr(gs, "embed_to_global", _fake_embed)

    def _install(retired=()):
        import empirica.core.lessons.storage as st

        monkeypatch.setattr(st, "get_lesson_storage", lambda: _Store(conn, retired))
        return conn, embedded

    yield _install
    conn.close()


def _add(conn, lid, policy, name="A lesson", description="body"):
    conn.execute(
        "INSERT INTO lessons (id, name, description, domain, sharing_policy, abstraction_level) VALUES (?,?,?,?,?,?)",
        (lid, name, description, "testing", policy, "cross_org"),
    )
    conn.commit()


# ── the gate is the authored policy ──────────────────────────────────────────


@pytest.mark.parametrize("policy", ["org", "public"])
def test_a_lesson_the_author_shared_propagates(wired, policy):
    conn, embedded = wired()
    _add(conn, "l1", policy)

    out = gs.sync_lessons_to_global("proj-1")

    assert out["synced"] == 1 and out["eligible"] == 1
    assert embedded[0]["type"] == "lesson", "it must land as a lesson, not as a finding"
    assert embedded[0]["project_id"] == "proj-1", "attribution — all 32 prior embeds had project_id None"


@pytest.mark.parametrize("policy", ["private", "project", "licensed"])
def test_a_lesson_the_author_did_not_share_stays_home(wired, policy):
    """`private`/`project` are refusals. `licensed` is a rights question this
    layer cannot answer, so it stays out rather than being quietly read as public."""
    conn, embedded = wired()
    _add(conn, "l1", policy)

    out = gs.sync_lessons_to_global("proj-1")

    assert out["eligible"] == 0 and out["synced"] == 0
    assert embedded == []


def test_impact_is_not_the_gate(wired):
    """NEGATIVE CONTROL for the design: no impact column is consulted at all.

    The finding sync selects on `impact >= 0.7`. If that model leaked into this
    one, a shared lesson would need a magnitude to travel — and lessons have no
    impact score, so every one of them would silently fail to publish.
    """
    conn, _embedded = wired()
    _add(conn, "l1", "public")

    assert gs.sync_lessons_to_global("proj-1")["synced"] == 1
    assert "impact" not in gs.sync_lessons_to_global("proj-1")


# ── superseded lessons do not travel ─────────────────────────────────────────


def test_a_retired_lesson_is_withheld_and_counted(wired):
    """The guard that must be shown to fire: it had never fired on real data."""
    conn, embedded = wired(retired={"old"})
    _add(conn, "old", "public")
    _add(conn, "live", "public")

    out = gs.sync_lessons_to_global("proj-1")

    assert out["eligible"] == 2
    assert out["withheld_superseded"] == 1
    assert out["synced"] == 1
    assert [e["item_id"] for e in embedded] == ["lesson_live"]


def test_withholding_is_reported_not_merely_done(wired):
    """A silent withhold is indistinguishable from nothing being eligible."""
    conn, _ = wired(retired={"old"})
    _add(conn, "old", "public")

    out = gs.sync_lessons_to_global("proj-1")
    assert out["synced"] == 0
    assert out["withheld_superseded"] == 1, "the zero above is explained by this number"


# ── the breakdown ────────────────────────────────────────────────────────────


def test_an_unavailable_backend_is_distinguishable_from_nothing_to_send(wired, monkeypatch):
    conn, _ = wired()
    _add(conn, "l1", "public")
    monkeypatch.setattr(gs, "_check_qdrant_available", lambda: False)

    out = gs.sync_lessons_to_global("proj-1")
    assert out["skipped_reason"] == "qdrant_unavailable"
    assert out["synced"] == 0 and out["eligible"] == 0


def test_a_failed_write_is_counted_as_failed_not_as_absent(wired, monkeypatch):
    """Peers not seeing a lesson because the write failed must not read as `synced: 0`."""
    conn, _ = wired()
    _add(conn, "l1", "public")
    monkeypatch.setattr(gs, "embed_to_global", lambda **kw: False)

    out = gs.sync_lessons_to_global("proj-1")
    assert out["eligible"] == 1 and out["failed"] == 1 and out["synced"] == 0


def test_an_unreadable_store_says_so_rather_than_reporting_success(wired, monkeypatch):
    wired()
    import empirica.core.lessons.storage as st

    def _boom():
        raise RuntimeError("no such table: lessons")

    monkeypatch.setattr(st, "get_lesson_storage", _boom)

    out = gs.sync_lessons_to_global("proj-1")
    assert out["skipped_reason"] and "store_unreadable" in out["skipped_reason"]


def test_an_empty_store_reports_zero_eligible_with_no_reason(wired):
    """NEGATIVE CONTROL: nothing to send is a clean zero, and says nothing went wrong."""
    wired()
    out = gs.sync_lessons_to_global("proj-1")
    assert out == {"synced": 0, "eligible": 0, "withheld_superseded": 0, "failed": 0, "skipped_reason": None}


# ── what actually gets embedded ──────────────────────────────────────────────


def test_the_embedded_text_carries_the_name_and_the_body(wired):
    """A peer searching semantically must hit a titled pattern, not a bare body."""
    conn, embedded = wired()
    _add(conn, "l1", "public", name="Read the authoritative surface", description="Not the convenient proxy.")

    gs.sync_lessons_to_global("proj-1")
    text = embedded[0]["text"]
    assert "Read the authoritative surface" in text
    assert "Not the convenient proxy." in text


def test_the_policy_that_authorised_it_rides_along(wired):
    """So a consumer can tell an org-scoped lesson from a public one after the fact."""
    conn, embedded = wired()
    _add(conn, "l1", "org")

    gs.sync_lessons_to_global("proj-1")
    assert "org" in embedded[0]["tags"]
    assert "lesson" in embedded[0]["tags"]


def test_resync_is_idempotent_on_the_point_id(wired):
    """The id is derived from the lesson id, so republishing updates rather than duplicates."""
    conn, embedded = wired()
    _add(conn, "l1", "public")

    gs.sync_lessons_to_global("proj-1")
    gs.sync_lessons_to_global("proj-1")

    assert [e["item_id"] for e in embedded] == ["lesson_l1", "lesson_l1"], "same id both times"


# ── publishing is not enough: they have to be SEEN ───────────────────────────


class _Pt:
    def __init__(self, score, type_, text):
        self.score = score
        self.payload = {"type": type_, "text": text}


class _Res:
    def __init__(self, pts):
        self.points = pts


class _Client:
    """Ranks by a caller-supplied script, so the merge logic is what's under test.

    HONOURS the filter's requested types. An earlier version returned the lesson
    list for ANY non-None filter, which made a finding-only query look like a
    lesson reservation and failed a correct implementation — a fake that lies
    about the backend produces failures indistinguishable from real ones.
    """

    def __init__(self, unfiltered, lessons):
        self._unfiltered, self._lessons = unfiltered, lessons
        self.lesson_queries = 0

    def collection_exists(self, _name) -> bool:
        return True

    @staticmethod
    def _requested_types(query_filter):
        types = set()
        for cond in getattr(query_filter, "must", None) or []:
            match = getattr(cond, "match", None)
            types |= set(getattr(match, "any", None) or [])
        return types

    def query_points(self, *, collection_name, query, query_filter=None, limit=10, with_payload=True):
        wanted = self._requested_types(query_filter)
        if wanted == {"lesson"}:
            self.lesson_queries += 1
            return _Res(self._lessons[:limit])
        pool = (
            self._unfiltered + self._lessons
            if not wanted
            else [p for p in self._unfiltered + self._lessons if p.payload["type"] in wanted]
        )
        if not wanted:
            pool = self._unfiltered
        return _Res(sorted(pool, key=lambda p: p.score, reverse=True)[:limit])


def test_a_competitive_lesson_surfaces_where_volume_had_buried_it():
    """The measured shape: 954 findings to 10 lessons, so a lesson loses on frequency.

    Nothing about the lesson's FIT changed — only whether it reached the ballot.
    """
    findings = [_Pt(0.60 - i * 0.01, "finding", f"f{i}") for i in range(5)]
    lessons = [_Pt(0.58, "lesson", "the right lesson")]
    client = _Client(findings, lessons)

    out = gs._reserve_lesson_slots(client, "c", [0.0], [gs._global_hit(p) for p in findings], 5, None)

    assert client.lesson_queries == 1
    assert [h["type"] for h in out].count("lesson") == 1
    assert out[0]["type"] == "finding", "a better-matching finding still ranks first"
    assert [h["score"] for h in out] == sorted((h["score"] for h in out), reverse=True)


def test_an_uncompetitive_lesson_is_not_forced_in():
    """NEGATIVE CONTROL, and the reason the docstring must not say 'ensures'.

    A hard floor would inject an irrelevant lesson into every cross-practice
    search — noise that trains practitioners to skip the lesson rows entirely.
    """
    findings = [_Pt(0.9 - i * 0.01, "finding", f"f{i}") for i in range(5)]
    lessons = [_Pt(0.1, "lesson", "unrelated")]
    out = gs._reserve_lesson_slots(
        _Client(findings, lessons), "c", [0.0], [gs._global_hit(p) for p in findings], 5, None
    )

    assert all(h["type"] == "finding" for h in out)


def test_no_second_query_when_the_quota_is_already_met():
    lessons_present = [_Pt(0.9, "lesson", "a"), _Pt(0.8, "lesson", "b"), _Pt(0.7, "finding", "c")]
    client = _Client(lessons_present, [])
    gs._reserve_lesson_slots(client, "c", [0.0], [gs._global_hit(p) for p in lessons_present], 5, None)

    assert client.lesson_queries == 0, "no wasted round-trip when lessons already rank"


def _search_with(monkeypatch, reserved: list, points, **kw):
    """Drive `search_global` against a fake client, spying on the reservation branch.

    The spy sits on `_reserve_lesson_slots` rather than counting filtered queries:
    `search_global` builds a query_filter for ANY `item_types`, so counting
    filtered calls would flag the finding-only case as a reservation and fail a
    correct implementation.
    """
    real = gs._reserve_lesson_slots

    def _spy(*a, **k):
        reserved.append(1)
        return real(*a, **k)

    client = _Client(points, [_Pt(0.58, "lesson", "competitive")])
    monkeypatch.setattr(gs, "_reserve_lesson_slots", _spy)
    monkeypatch.setattr(gs, "_check_qdrant_available", lambda: True)
    monkeypatch.setattr(gs, "_get_embedding_safe", lambda _t: [0.0])
    monkeypatch.setattr(gs, "_get_qdrant_client", lambda: client)
    return gs.search_global("anything", limit=5, **kw)


def test_an_explicit_type_filter_is_respected(monkeypatch):
    """A caller asking for findings only must not be handed lessons anyway.

    Asserted through `search_global`, the real entry point. The first draft of
    this test monkeypatched the helper and asserted an empty call list without
    ever invoking the function — green, and proving nothing. That is the vacuous
    shape this file exists to catch, so it is named here rather than quietly
    rewritten.
    """
    reserved = []
    out = _search_with(monkeypatch, reserved, [_Pt(0.6, "finding", "f")], item_types=["finding"])

    assert reserved == [], "an explicit type filter must suppress the reservation entirely"
    assert all(h["type"] == "finding" for h in out)


def test_without_a_type_filter_the_reservation_does_run(monkeypatch):
    """POSITIVE CONTROL for the test above — otherwise it passes on a dead path."""
    reserved = []
    _search_with(monkeypatch, reserved, [_Pt(0.6, "finding", "f")])

    assert len(reserved) == 1, "with no type filter the reservation must run"


def test_the_reservation_fails_open():
    """A retrieval nicety must never be able to empty a result set."""

    class _Boom:
        def query_points(self, **_):
            raise RuntimeError("qdrant down")

    original = [{"type": "finding", "text": "f", "score": 0.5}]
    assert gs._reserve_lesson_slots(_Boom(), "c", [0.0], original, 5, None) == original


def test_a_duplicate_lesson_is_not_added_twice():
    both = [_Pt(0.6, "lesson", "same"), _Pt(0.5, "finding", "f")]
    client = _Client(both, [_Pt(0.6, "lesson", "same")])
    out = gs._reserve_lesson_slots(client, "c", [0.0], [gs._global_hit(p) for p in both], 5, None)

    assert [h["text"] for h in out].count("same") == 1

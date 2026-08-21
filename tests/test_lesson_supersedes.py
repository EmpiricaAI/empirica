"""`supersedes` had a schema, a reader, and no writer — so nothing was ever retired.

The relation has been in `knowledge_graph` since the lesson graph landed
(`schema.py:53`, `storage.py:1024`, counted in `get_lesson_graph`). Nothing in the
codebase ever created one, and no read path consulted it. A lesson replaced by a
better one kept surfacing in search exactly like the live one, with nothing on the
served row to say it had been retired.

That is the archive-not-a-model failure inside the lesson layer: a store that only
ever grows returns superseded guidance as current, and the practitioner acts on it.

Two halves, and both must hold or the feature is theatre:

1. **Write** — `lesson-create` accepts `supersedes`, and validates the target
   EXISTS before writing. An edge pointing at nothing suppresses nothing while
   reporting success, which is worse than refusing: the practitioner believes the
   old guidance is retired and it keeps being served.
2. **Read** — `search_lessons` marks and (by default) withholds them, and the CLI
   reports the count. A filter that drops rows silently is indistinguishable from
   having nothing to show.
"""

from __future__ import annotations

from argparse import Namespace

import pytest

from empirica.cli.command_handlers.lesson_commands import _supersession_note, _wire_supersession
from empirica.core.lessons.storage import _apply_supersession


class _FakeStore:
    """Only what the two units touch: existence, edge-writing, the retired map."""

    def __init__(self, existing=(), edge_ok=True):
        self._existing = set(existing)
        self._edge_ok = edge_ok
        self.edges: list[tuple] = []

    def get_lesson(self, lid):
        return object() if lid in self._existing else None

    def add_edge(self, source_id, target_id, relation_type, **_):
        self.edges.append((source_id, target_id, relation_type))
        return "edge-id" if self._edge_ok else None

    def superseded_ids(self):
        return {t: s for s, t, rel in self.edges if rel == "supersedes"}


# ── write side ───────────────────────────────────────────────────────────────


def test_the_edge_is_written_when_the_target_exists():
    store = _FakeStore(existing={"old"})
    written, err = _wire_supersession(store, "new", "old")

    assert written is True and err is None
    assert store.edges == [("new", "old", "supersedes")], "source supersedes target, not the reverse"


def test_an_edge_to_a_nonexistent_lesson_is_refused_and_named():
    """The dangerous case: it would suppress nothing while reporting success."""
    store = _FakeStore(existing=set())
    written, err = _wire_supersession(store, "new", "ghost")

    assert written is None
    assert "no lesson with id" in err and "ghost" in err
    assert store.edges == [], "nothing written"


def test_a_lesson_cannot_supersede_itself():
    """Self-supersession would withhold the lesson from every search that finds it."""
    store = _FakeStore(existing={"same"})
    written, err = _wire_supersession(store, "same", "same")

    assert written is None and "cannot supersede itself" in err
    assert store.edges == []


def test_a_failed_write_is_reported_as_failed_not_as_absent():
    """`None` means not attempted and `False` means attempted-and-lost — different facts."""
    store = _FakeStore(existing={"old"}, edge_ok=False)
    written, err = _wire_supersession(store, "new", "old")

    assert written is False, "attempted and failed — distinct from the None cases above"
    assert "could not be written" in err


def test_no_declaration_is_silent():
    assert _wire_supersession(_FakeStore(), "new", None) == (None, None)
    assert _wire_supersession(_FakeStore(), "new", "  ") == (None, None), "whitespace is not a declaration"


# ── read side ────────────────────────────────────────────────────────────────


def test_a_superseded_lesson_is_withheld_by_default():
    rows = [{"id": "old", "name": "A"}, {"id": "live", "name": "B"}]
    out = _apply_supersession(rows, {"old": "new"}, include=False)

    assert [r["id"] for r in out] == ["live"]


def test_including_them_still_marks_them():
    """Returning retired guidance UNLABELLED would defeat the whole point."""
    rows = [{"id": "old", "name": "A"}, {"id": "live", "name": "B"}]
    out = _apply_supersession(rows, {"old": "new"}, include=True)

    assert [r["id"] for r in out] == ["old", "live"]
    assert out[0]["superseded_by"] == "new"
    assert "superseded_by" not in out[1]


def test_an_empty_supersession_map_changes_nothing():
    """NEGATIVE CONTROL: with nothing retired, both modes return the input intact."""
    rows = [{"id": "a"}, {"id": "b"}]
    assert _apply_supersession(list(rows), {}, include=False) == rows
    assert _apply_supersession(list(rows), {}, include=True) == rows


def test_superseded_ids_maps_target_to_source():
    """The direction is the whole meaning: the TARGET is the one retired."""
    store = _FakeStore(existing={"old"})
    _wire_supersession(store, "new", "old")

    assert store.superseded_ids() == {"old": "new"}


# ── the report ───────────────────────────────────────────────────────────────


def test_the_read_surface_says_what_it_withheld():
    store = _FakeStore(existing={"old"})
    _wire_supersession(store, "new", "old")

    assert _supersession_note(store, include_superseded=False) == {
        "superseded_in_store": 1,
        "superseded_withheld": True,
    }


def test_it_reports_a_zero_rather_than_omitting_the_key():
    """ "Nothing is retired" and "the key is absent" are different statements.

    Only one of them is checkable, and the absent-key form is how a filter comes
    to look like an empty result set.
    """
    note = _supersession_note(_FakeStore(), include_superseded=False)
    assert note["superseded_in_store"] == 0
    assert note["superseded_withheld"] is False


def test_asking_for_them_reports_that_nothing_was_withheld():
    store = _FakeStore(existing={"old"})
    _wire_supersession(store, "new", "old")

    note = _supersession_note(store, include_superseded=True)
    assert note["superseded_in_store"] == 1, "still says how many are retired"
    assert note["superseded_withheld"] is False


# ── the flag reaches the handler ─────────────────────────────────────────────


@pytest.mark.parametrize("verb", ["lesson_list", "lesson_search"])
def test_the_cli_flag_is_declared_on_both_read_verbs(verb):
    """A flag the parser advertises and the handler never reads is an advertised no-op."""
    import argparse

    from empirica.cli.parsers.lesson_parsers import add_lesson_parsers

    sub = argparse.ArgumentParser().add_subparsers(dest="command")
    add_lesson_parsers(sub)
    parser = sub.choices[verb.replace("_", "-")]
    args = parser.parse_args(["--include-superseded"])
    assert args.include_superseded is True


def test_the_handler_reads_the_flag(monkeypatch):
    """Asserted through the handler, so a parsed-but-ignored flag fails here."""
    import empirica.cli.command_handlers.lesson_commands as lc

    seen = {}

    class _S:
        def search_lessons(self, **kw):
            seen.update(kw)
            return []

        def superseded_ids(self):
            return {}

    monkeypatch.setattr(lc, "get_lesson_storage", lambda: _S(), raising=False)
    monkeypatch.setitem(__import__("sys").modules["empirica.core.lessons"].__dict__, "get_lesson_storage", lambda: _S())
    lc.handle_lesson_list_command(Namespace(domain=None, limit=5, include_superseded=True))
    assert seen.get("include_superseded") is True

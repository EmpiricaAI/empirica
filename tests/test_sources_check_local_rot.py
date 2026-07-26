"""`sources-check` must see LOCAL file rot, not just URL rot.

The probe gated on http(s), so file-backed sources were skipped entirely: one
practice reported "all probed source links resolve" while 25 of its 50 sources
could not be served at all. A source whose file is gone is exactly as dead as a 404.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.sources_check_commands import (
    _classify_local_source,
    _looks_like_a_path,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("docs/01_START_HERE.md", True),
        ("/abs/path/file.md", True),
        ("notes.md", True),
        ("LarQL — Neural Model as Database", False),  # a title, never a file
        ("Empirica: Reliable AI Architecture", False),
        ("some sentence with. a dot", False),
    ],
)
def test_looks_like_a_path_separates_locators_from_prose(value, expected):
    assert _looks_like_a_path(value) is expected


def test_existing_file_relative_to_project_root(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("x", encoding="utf-8")
    # stored WITHOUT the docs/ prefix — resolved via _SOURCE_PATH_PREFIXES
    assert _classify_local_source("guide.md", tmp_path)[0] == "ok"
    assert _classify_local_source("docs/guide.md", tmp_path)[0] == "ok"


def test_absolute_path_that_is_gone_is_missing(tmp_path):
    category, detail = _classify_local_source(str(tmp_path / "vanished.md"), tmp_path)
    assert category == "missing"
    assert "not on disk" in detail


def test_relative_path_not_found_is_missing(tmp_path):
    category, detail = _classify_local_source("docs/never_existed.md", tmp_path)
    assert category == "missing"
    assert "tried" in detail


def test_title_in_source_url_is_not_a_locator_rather_than_missing(tmp_path):
    """These need RE-POINTING, not a file hunt — so they must not be lumped in with
    genuinely-missing files."""
    category, detail = _classify_local_source("LarQL — Neural Model as Database", tmp_path)
    assert category == "not_a_locator"
    assert "title" in detail


def test_no_project_root_still_classifies(tmp_path):
    """A relative path with no resolvable root is missing, not a crash."""
    assert _classify_local_source("docs/x.md", None)[0] == "missing"


def test_prefixes_match_the_daemon(tmp_path):
    """These must mirror the daemon's resolution or the two disagree about whether a
    source is rotted — the exact class of divergence this work keeps hitting."""
    from empirica.api.routes.artifacts import _SOURCE_PATH_PREFIXES as daemon_prefixes
    from empirica.cli.command_handlers.sources_check_commands import _SOURCE_PATH_PREFIXES as check_prefixes

    assert set(check_prefixes) == set(daemon_prefixes)


# ── review cadence (timestamped verdicts) ─────────────────────────────


def test_stamp_reviews_writes_timestamped_verdicts(tmp_path, monkeypatch):
    """A source nobody has verified is an assertion with a date on it, not ground
    truth. Stamping is what turns sources-check into a CADENCE (decision f5c59ec8)."""
    import sqlite3

    from empirica.cli.command_handlers import sources_check_commands as sc

    db_file = tmp_path / "s.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE epistemic_sources (id TEXT PRIMARY KEY, last_reviewed_at TEXT, review_verdict TEXT)")
    conn.executemany("INSERT INTO epistemic_sources (id) VALUES (?)", [("a",), ("b",)])
    conn.commit()
    conn.close()

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(str(db_file))

        def close(self):
            self.conn.close()

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _DB)
    stamped = sc._stamp_reviews({"a": "live", "b": "missing"})

    assert stamped == 2
    conn = sqlite3.connect(str(db_file))
    rows = dict(conn.execute("SELECT id, review_verdict FROM epistemic_sources").fetchall())
    times = [r[0] for r in conn.execute("SELECT last_reviewed_at FROM epistemic_sources").fetchall()]
    conn.close()
    assert rows == {"a": "live", "b": "missing"}
    assert all(t for t in times), "every checked source must carry a review timestamp"


def test_stamp_reviews_tolerates_a_db_predating_the_columns(tmp_path, monkeypatch):
    """An older practice DB must not fail the check just because it can't record."""
    import sqlite3

    from empirica.cli.command_handlers import sources_check_commands as sc

    db_file = tmp_path / "old.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("CREATE TABLE epistemic_sources (id TEXT PRIMARY KEY)")  # no review columns
    conn.execute("INSERT INTO epistemic_sources (id) VALUES ('a')")
    conn.commit()
    conn.close()

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(str(db_file))

        def close(self):
            self.conn.close()

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _DB)
    assert sc._stamp_reviews({"a": "live"}) == 0  # degrades, does not raise


def test_stamp_reviews_noop_on_empty(tmp_path):
    from empirica.cli.command_handlers import sources_check_commands as sc

    assert sc._stamp_reviews({}) == 0

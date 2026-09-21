"""A bare SessionDatabase() under test must not be the developer's live store.

Three test files reached one through GitEnhancedReflexLogger and wrote 9264
reflex rows into the checkout's sessions.db between 2026-06-23 and 2026-09-21,
under the git-remote-hash fallback project id. 685 were POSTFLIGHTs that never
got a verification, and counted beside real ones they read as a calibration
outage. conftest now pins EMPIRICA_SESSION_DB for the whole suite.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_the_default_store_is_outside_the_checkout_and_home():
    from empirica.config.path_resolver import get_session_db_path

    resolved = get_session_db_path().resolve()
    assert REPO not in resolved.parents, resolved
    assert (Path.home() / ".empirica").resolve() not in resolved.parents, resolved


def test_a_bare_session_database_opens_the_pinned_file():
    from empirica.config.path_resolver import get_session_db_path
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase()
    try:
        opened = Path(db.conn.execute("PRAGMA database_list").fetchone()[2]).resolve()
    finally:
        db.close()
    assert opened == get_session_db_path().resolve()
    assert REPO not in opened.parents


def test_positive_control_the_pin_is_what_moves_it(monkeypatch):
    """Without the variable the resolver goes somewhere else, so the pin is load-bearing."""
    from empirica.config.path_resolver import get_session_db_path

    pinned = get_session_db_path().resolve()
    monkeypatch.delenv("EMPIRICA_SESSION_DB")
    try:
        unpinned = get_session_db_path().resolve()
    except Exception:
        return  # no project resolvable at all is also "somewhere else"
    assert unpinned != pinned

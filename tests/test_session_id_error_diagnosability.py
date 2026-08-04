"""An error that identifies a record by a PREFIX cannot report a mismatch outside it.

Reported by cortex (prop_mbj3uva). A pointer file held a UUID with one dropped
character, in the LAST segment:

    sessions table :  4aea7003-12ab-4c37-81bb-3030ce39f1bb   (36)
    tmux_8 pointer :  4aea7003-12ab-4c37-81bb-3030ce9f1bb    (35)

POSTFLIGHT's precondition error printed `session_id[:8]`, so the message read
`session 4aea7003 not found` — the REAL session's prefix. Everyone who read it
checked whether `4aea7003` existed, found it intact and correctly bound, and
concluded the resolver was broken. Three practices searched the wrong component.

The message did not merely fail to help. It reliably accused the wrong thing,
because the part it showed WAS correct.

Two fixes, both tested here:
  * print the identifier in full, and name a near-miss row when one exists
  * warn at PREFLIGHT, where the bad id ENTERS, instead of only at POSTFLIGHT

Note what is deliberately NOT done: cortex suggested UUID validation. Three of
1037 live session_ids are not UUIDs (`latest`, `investigation-cli-mapping`, and a
bare 8-char prefix), so shape validation would reject legitimate sessions.
Existence is the invariant; shape is not.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.cli.command_handlers._workflow_postflight import (
    _near_miss_session,
    _session_not_found_message,
)

_REAL = "4aea7003-12ab-4c37-81bb-3030ce39f1bb"
_TYPO = "4aea7003-12ab-4c37-81bb-3030ce9f1bb"  # the '3' dropped, last segment


def _cursor(session_ids=(_REAL,)):
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sessions (session_id TEXT)")
    conn.executemany("INSERT INTO sessions VALUES (?)", [(s,) for s in session_ids])
    conn.commit()
    return conn.cursor()


# ─── the reported defect ──────────────────────────────────────────────────


def test_the_full_identifier_is_printed_not_a_prefix():
    """The whole point. A prefix hides exactly the character that differs."""
    msg = _session_not_found_message(_cursor(), _TYPO)

    assert _TYPO in msg, "the failing id must appear in full, or the reader cannot see the typo"


def test_the_message_does_not_show_only_the_shared_prefix():
    """Regression on the exact misdirection: printing `4aea7003` alone sends the
    reader to verify a row that is fine."""
    msg = _session_not_found_message(_cursor(), _TYPO)

    assert msg.count(_TYPO) >= 1
    # The old message was ~40 chars and contained the 8-char prefix and nothing
    # else identifying. A message that still cannot distinguish the two ids fails.
    assert _REAL in msg, "the near-match must be named — that is what converts a misdiagnosis into a fix"


def test_the_near_miss_is_identified():
    near = _near_miss_session(_cursor(), _TYPO)

    assert near == _REAL


def test_the_message_says_the_pointer_is_the_suspect_not_the_row():
    """Directing the reader at the right component IS the fix. Without this the
    message is merely more verbose, not more useful."""
    msg = _session_not_found_message(_cursor(), _TYPO)

    assert "pointer" in msg.lower()
    assert "active_transaction" in msg


def test_lengths_are_shown_because_the_difference_is_one_character():
    msg = _session_not_found_message(_cursor(), _TYPO)

    assert "35" in msg and "36" in msg


# ─── the near-miss detector must not over-claim ───────────────────────────


def test_a_genuinely_different_session_is_not_reported_as_a_near_miss():
    """A false near-miss is worse than none: it would send the reader to fix a
    pointer that is correct."""
    other = "4aea7003-0000-0000-0000-000000000000"  # same prefix, unrelated
    near = _near_miss_session(_cursor([other]), _TYPO)

    assert near is None


def test_no_near_miss_when_nothing_is_close():
    near = _near_miss_session(_cursor(["deadbeef-1111-2222-3333-444444444444"]), _TYPO)

    assert near is None


def test_short_ids_do_not_crash_the_detector():
    """`latest` and a bare 8-char prefix are real session_ids in this store."""
    for weird in ("latest", "2bc1da78", "", "investigation-cli-mapping"):
        assert _near_miss_session(_cursor(), weird) is None


# ─── PREFLIGHT: warn where the corruption enters ──────────────────────────


def test_preflight_warns_when_the_session_row_is_missing(monkeypatch):
    from empirica.cli.command_handlers import _workflow_preflight as pf

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute("CREATE TABLE sessions (session_id TEXT)")
            self.conn.execute("INSERT INTO sessions VALUES (?)", (_REAL,))
            self.conn.commit()

        def close(self):
            self.conn.close()

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _DB)

    warning = pf._preflight_check_session_exists(_TYPO)

    assert warning is not None, "a transaction opening against a nonexistent session must say so"
    assert warning["near_miss"] == _REAL
    assert warning["length"] == 35
    assert "POSTFLIGHT" in warning["message"], "say what will break, not just that something is wrong"


def test_preflight_is_silent_when_the_session_exists(monkeypatch):
    """No noise on the normal path — a warning that always fires is ignored."""
    from empirica.cli.command_handlers import _workflow_preflight as pf

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute("CREATE TABLE sessions (session_id TEXT)")
            self.conn.execute("INSERT INTO sessions VALUES (?)", (_REAL,))
            self.conn.commit()

        def close(self):
            self.conn.close()

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _DB)

    assert pf._preflight_check_session_exists(_REAL) is None


def test_preflight_never_blocks_on_a_diagnostic_failure(monkeypatch):
    """PREFLIGHT is hot-path. A diagnostic that raises must not refuse the work —
    a validation regression that blocks legitimate transactions is worse than the
    silent bug it was added to prevent.
    """
    from empirica.cli.command_handlers import _workflow_preflight as pf

    def _boom():
        raise RuntimeError("db unavailable")

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _boom)

    assert pf._preflight_check_session_exists(_TYPO) is None


# ─── the mint point: the inverted safety in _resolve_partial_uuid ──────────


def test_one_edit_detector_handles_the_real_reported_id():
    """The regression that mattered: the dropped digit falls INSIDE the last six
    characters, which a tail-comparison heuristic cannot see."""
    from empirica.utils.session_resolver import within_one_edit

    assert within_one_edit(_REAL, _TYPO)
    assert within_one_edit(_TYPO, _REAL)


def test_one_edit_detector_rejects_larger_differences():
    from empirica.utils.session_resolver import within_one_edit

    assert not within_one_edit(_REAL, _REAL)  # identical is not a near-miss
    assert not within_one_edit(_REAL, "4aea7003-0000-0000-0000-000000000000")
    assert not within_one_edit(_REAL, _REAL[:30])  # two+ chars missing
    assert not within_one_edit("", "")


def test_one_edit_detector_catches_substitution_not_just_deletion():
    from empirica.utils.session_resolver import within_one_edit

    swapped = _REAL[:20] + ("0" if _REAL[20] != "0" else "1") + _REAL[21:]
    assert within_one_edit(_REAL, swapped)


def test_a_full_looking_id_that_does_not_exist_now_raises(monkeypatch):
    """The inverted safety. Before this, an 8-char partial was DB-checked while a
    full id with a typo was returned unchecked — typing LESS of the id was the
    safe move. Raising here is consistency with the partial branch, which has
    always raised, not a new restriction.
    """
    import sqlite3

    from empirica.utils import session_resolver as sr

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute("CREATE TABLE sessions (session_id TEXT, start_time REAL)")
            self.conn.execute("INSERT INTO sessions VALUES (?, 1.0)", (_REAL,))
            self.conn.commit()

        def close(self):
            self.conn.close()

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _DB)

    assert sr._resolve_partial_uuid(_REAL) == _REAL

    with pytest.raises(ValueError) as exc:
        sr._resolve_partial_uuid(_TYPO)

    msg = str(exc.value)
    assert _TYPO in msg, "the rejected id must be shown in full"
    assert _REAL in msg, "the near-match must be named"


def test_resolution_does_not_block_when_the_db_is_unreachable(monkeypatch):
    """An unverifiable session must not fail the way a typo does. A diagnostic
    that becomes the fault is worse than the bug it detects.
    """
    from empirica.utils import session_resolver as sr

    def _boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _boom)

    assert sr._resolve_partial_uuid(_REAL) == _REAL


# ─── the narrowing: typo vs "id this DB has not seen" ─────────────────────
#
# Raising on ANY absent full id broke tests/test_session_resolver.py's
# test_resolve_full_uuid, and that failure was informative rather than annoying:
# a well-formed UUID absent from THIS project's DB may be a legitimate id from
# another project, and refusing it would be a hot-path regression traded for a
# typo fix. So the raise is narrowed to ids that are malformed or one edit from a
# stored row — both of which the reported corruption is.


def _resolver_with(monkeypatch, stored):
    import sqlite3

    from empirica.utils import session_resolver as sr

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute("CREATE TABLE sessions (session_id TEXT, start_time REAL)")
            self.conn.executemany("INSERT INTO sessions VALUES (?, 1.0)", [(s,) for s in stored])
            self.conn.commit()

        def close(self):
            self.conn.close()

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _DB)
    return sr


def test_a_malformed_id_raises_even_with_no_near_miss(monkeypatch):
    """35 chars with a dropped digit is not a plausible foreign id."""
    sr = _resolver_with(monkeypatch, ["deadbeef-1111-2222-3333-444444444444"])

    with pytest.raises(ValueError, match="well-formed"):
        sr._resolve_partial_uuid(_TYPO)


def test_a_wellformed_unknown_id_passes_through(monkeypatch):
    """Preserves cross-project resolution. This is the case whose test failure
    caught the over-broad first version of the rule."""
    sr = _resolver_with(monkeypatch, ["deadbeef-1111-2222-3333-444444444444"])
    stranger = "88dbf132-cc7c-4a4b-9b59-77df3b13dbd2"

    assert sr._resolve_partial_uuid(stranger) == stranger


def test_a_wellformed_id_one_edit_from_a_stored_row_still_raises(monkeypatch):
    """The near-miss signal outranks well-formedness — a single substitution
    inside a valid UUID is a typo, not a foreign session."""
    sr = _resolver_with(monkeypatch, [_REAL])
    swapped = _REAL[:20] + ("0" if _REAL[20] != "0" else "1") + _REAL[21:]

    assert len(swapped) == 36
    with pytest.raises(ValueError) as exc:
        sr._resolve_partial_uuid(swapped)
    assert _REAL in str(exc.value)


def test_non_uuid_aliases_are_untouched_by_the_shape_test(monkeypatch):
    """`latest` and friends must never be judged on UUID shape — they route
    through the alias path, and three such ids are live in this store."""
    from empirica.utils.session_resolver import _is_uuid_shaped

    for alias in ("latest", "investigation-cli-mapping", "2bc1da78"):
        assert not _is_uuid_shaped(alias)


# ─── rejected input is not a defect ───────────────────────────────────────


def test_a_rejected_id_raises_the_typed_error_not_a_bare_ValueError(monkeypatch):
    """Auto-capture files every CLI failure as HIGH severity, and the release gate
    blocks on HIGH. So "the CLI correctly rejected a typo" and "the CLI crashed"
    must be distinguishable at the type level.

    Found the hard way: making resolution strict turned a contract test that
    deliberately passes `nonexistent-session-id` into 8 high-severity issues per
    suite run, written to the live project, which blocked `release.py --prepare`.
    """
    from empirica.utils import session_resolver as sr

    class _DB:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self.conn.execute("CREATE TABLE sessions (session_id TEXT, start_time REAL)")
            self.conn.execute("INSERT INTO sessions VALUES (?, 1.0)", (_REAL,))
            self.conn.commit()

        def close(self):
            self.conn.close()

    monkeypatch.setattr("empirica.data.session_database.SessionDatabase", _DB)

    with pytest.raises(sr.InvalidSessionIdError):
        sr._resolve_partial_uuid(_TYPO)

    # Subclass of ValueError, so every existing `except ValueError` around session
    # resolution keeps working — including _resolve_and_validate_session, which
    # turns it into the CLI's ok:false envelope.
    assert issubclass(sr.InvalidSessionIdError, ValueError)


def test_bad_input_is_captured_at_low_severity_not_high():
    """LOW, not silent. A flood of rejected ids is worth seeing (a broken caller,
    a bad script) — only the severity was wrong, so dropping the capture entirely
    would trade one blind spot for another.
    """
    import inspect

    from empirica.cli import cli_utils

    src = inspect.getsource(cli_utils.handle_cli_error)

    assert "InvalidSessionIdError" in src, "the handler must distinguish rejected input from a defect"
    assert "IssueSeverity.LOW" in src
    assert "IssueSeverity.HIGH" in src, "genuine failures must still capture HIGH"

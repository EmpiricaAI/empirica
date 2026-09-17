"""Code that is OLDER than the database it writes to must say so.

On 2026-09-17 migration 071 added `scope` and `measured_count` to
`transaction_claims`. It was applied by an editable install running from the
working tree. The `empirica` binary on PATH was a frozen 1.13.46 copy sharing the
same database. Pydantic accepted the new keys — `claims: list[dict]` takes anything
— the old writer ignored them, and every CLI call reported ok while storing NULL.

Several practices measured it independently ("columns exist, populated on 0 of
1,111 claims; PREFLIGHT accepts both keys and silently stores neither") and it read
as a defect in the new code. The new code was correct the whole time. Timestamped
on the box where it was diagnosed:

    23:25:27  NULL   frozen 1.13.46 binary
    23:27:08  NULL
    23:28:25  NULL
    23:28:40  -- venv reinstalled editable --
    23:29:41  stored

**There was no bug anywhere.** A newer schema, an older writer, and an input model
permissive enough to let them disagree in silence. Accepted-and-discarded produced
purely by version skew — which means no handler-level test could ever have caught
it, and the only place it is detectable is where code meets schema.

The diagnosis itself was nearly lost to the same shape: the running install was
first "verified" with `python -c "import empirica"` executed FROM THE REPO
DIRECTORY, where `''` on `sys.path` finds `./empirica` regardless of what is
installed. It reported the working tree and ruled out deploy-staleness, wrongly.
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.data.migrations import migration_runner as mr
from empirica.data.migrations.migration_runner import MigrationRunner


@pytest.fixture(autouse=True)
def _reset_once_flag():
    """The warning fires once per PROCESS, so every test needs a clean flag."""
    mr._SKEW_WARNED = False
    yield
    mr._SKEW_WARNED = False


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    yield c
    c.close()


def _noop(_cursor):
    return None


KNOWN = [("001_a", "first", _noop), ("002_b", "second", _noop)]


def test_a_database_newer_than_the_code_is_detected(conn):
    """Today's case: another install applied a migration this code never shipped."""
    r = MigrationRunner(conn)
    r.run_all(KNOWN)
    r.mark_as_run("071_claim_scope_and_count", "applied by a NEWER install")

    assert r.schema_ahead_of_code(KNOWN) == ["071_claim_scope_and_count"]


def test_the_skew_is_announced_on_stderr(conn, capsys):
    r = MigrationRunner(conn)
    r.run_all(KNOWN)
    r.mark_as_run("071_claim_scope_and_count", "applied by a NEWER install")
    capsys.readouterr()

    r.run_all(KNOWN)
    err = capsys.readouterr().err

    assert "NEWER than the code" in err
    assert "071_claim_scope_and_count" in err, "name the migration — a count cannot be acted on"
    assert "ACCEPTED and silently NOT STORED" in err, "say what the consequence is, not just that skew exists"
    assert "report ok" in err, "the dangerous part is that nothing will look wrong"


def test_matching_code_and_schema_is_silent(conn, capsys):
    """Positive control.

    A warning on every healthy open would satisfy the test above and be ignored
    within a day. This is the case every normal run is in.
    """
    r = MigrationRunner(conn)
    r.run_all(KNOWN)
    capsys.readouterr()

    r.run_all(KNOWN)

    assert capsys.readouterr().err == ""
    assert r.schema_ahead_of_code(KNOWN) == []


def test_code_NEWER_than_schema_is_not_skew(conn, capsys):
    """The ordinary upgrade path must stay quiet.

    Code that knows MORE migrations than the DB has applied simply applies them.
    Only the reverse is dangerous; warning on both would make every upgrade noisy.
    """
    r = MigrationRunner(conn)
    r.run_all(KNOWN[:1])
    capsys.readouterr()

    r.run_all(KNOWN)

    assert capsys.readouterr().err == ""


def test_the_warning_fires_once_per_process(conn, capsys):
    """A process opens the DB many times. Saying it on every connection turns a
    signal into wallpaper."""
    r = MigrationRunner(conn)
    r.run_all(KNOWN)
    r.mark_as_run("099_future", "newer install")
    capsys.readouterr()

    r.run_all(KNOWN)
    first = capsys.readouterr().err
    r.run_all(KNOWN)
    second = capsys.readouterr().err

    assert "NEWER than the code" in first
    assert second == ""


def test_skew_warns_and_does_NOT_refuse_the_database(conn):
    """An older reader is usually harmless, and refusing to open would convert a
    recoverable skew into an outage. It must be loud, not fatal."""
    r = MigrationRunner(conn)
    r.run_all(KNOWN)
    r.mark_as_run("099_future", "newer install")

    r.run_all(KNOWN)  # must not raise


def test_a_renamed_migration_id_is_residue_not_a_newer_install(conn, capsys):
    """The guard's own first false alarm, kept as a regression.

    On its first real run it reported "a newer install migrated this DB" for
    `068_goal_completion_reason_` — a trailing underscore, applied from an
    uncommitted tree and renamed before commit. Harmless residue, and the message
    asserted a cause the code had never observed: the defect this area exists to
    remove, reproduced inside its own guard.

    A genuinely newer install's migration is numbered BEYOND what this code ships.
    An unknown id at or below the code's highest number was renamed or removed.
    """
    r = MigrationRunner(conn)
    r.run_all(KNOWN)
    r.mark_as_run("002_b_", "same number, renamed before commit")
    capsys.readouterr()

    r.run_all(KNOWN)

    assert capsys.readouterr().err == "", "rename residue is not evidence of another install"
    assert r.schema_ahead_of_code(KNOWN) == []
    assert r._unknown_ids(KNOWN)["residue"] == ["002_b_"], "but it must still be visible to a caller who asks"


def test_newer_and_residue_are_told_apart_in_one_database(conn):
    r = MigrationRunner(conn)
    r.run_all(KNOWN)
    r.mark_as_run("001_a_old_name", "residue")
    r.mark_as_run("071_from_a_newer_install", "newer")

    out = r._unknown_ids(KNOWN)

    assert out == {"newer": ["071_from_a_newer_install"], "residue": ["001_a_old_name"]}

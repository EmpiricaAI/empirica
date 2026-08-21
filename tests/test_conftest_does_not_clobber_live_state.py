"""The isolation fixture must not eat the state it is isolating from.

`conftest.isolate_empirica_instance` snapshots every `active_transaction*.json`
under `$HOME` and the cwd at session start and restores them at teardown. That
restore was unconditional, and on 2026-08-21 it ate a live transaction:

- the release gate runs a ~12-minute suite;
- a PREFLIGHT submitted inside that window rewrote `active_transaction_tmux_6.json`
  to the newly opened transaction;
- teardown wrote the pre-run snapshot back over it;
- the practitioner's POSTFLIGHT read the PREVIOUS, already-closed transaction, so
  its two declared claims kept NULL verdicts and its artifact counts were reported
  against the wrong window.

Two writers, one key, last-write-wins — and the writer who lost was the one doing
real work. The only visible trace was a `dropped_adjudications` note that reads
like a payload-shape error, which is why it survived being looked at directly.

The rule under test: **restore only what nobody else wrote to.** A snapshot is an
undo for this fixture's own escapes, never a licence to revert a stranger.
"""

from __future__ import annotations

import json
import os

import pytest

from tests.conftest import _restore_untouched_transaction_files


def _snapshot(path):
    """The shape conftest records: contents plus the mtime they were read at."""
    return {str(path): (path.read_text(), os.stat(path).st_mtime_ns)}


def _write(path, tx, status="open"):
    path.write_text(json.dumps({"transaction_id": tx, "status": status}))
    return path


def test_a_file_a_live_writer_changed_is_left_alone(tmp_path):
    """The exact incident: PREFLIGHT lands mid-run, teardown must not revert it."""
    f = _write(tmp_path / "active_transaction_tmux_6.json", "OLD", status="closed")
    backup = _snapshot(f)

    os.utime(f, ns=(0, os.stat(f).st_mtime_ns + 1_000_000))  # a later writer...
    _write(f, "NEW")  # ...opens a new transaction
    os.utime(f, ns=(0, os.stat(f).st_mtime_ns + 2_000_000))

    left = _restore_untouched_transaction_files(backup)

    assert json.loads(f.read_text())["transaction_id"] == "NEW", "the live writer must win"
    assert left == [str(f)], "and the skip must be reported, not silent"


def test_the_unconditional_restore_would_fail_this(tmp_path):
    """NEGATIVE CONTROL — the pre-fix behaviour, inline, so the guard is shown to bite.

    Without this the test above passes trivially against any implementation that
    happens not to write, and a guard that has never failed is not a guard.
    """
    f = _write(tmp_path / "active_transaction_tmux_6.json", "OLD", status="closed")
    contents = f.read_text()
    _write(f, "NEW")

    with open(f, "w") as fh:  # what conftest used to do, unconditionally
        fh.write(contents)

    assert json.loads(f.read_text())["transaction_id"] == "OLD", (
        "the pre-fix restore reverts the live writer — this is the defect, reproduced"
    )


def test_an_untouched_file_is_still_restored(tmp_path):
    """The fixture keeps its purpose: a test that escapes the pin is still undone."""
    f = _write(tmp_path / "active_transaction_tmux_6.json", "REAL")
    backup = _snapshot(f)

    # Simulate an escaped test overwriting it WITHOUT the mtime moving — the
    # only case where restoring is unambiguously right.
    mtime = os.stat(f).st_mtime_ns
    _write(f, "ESCAPED")
    os.utime(f, ns=(mtime, mtime))

    assert _restore_untouched_transaction_files(backup) == []
    assert json.loads(f.read_text())["transaction_id"] == "REAL"


def test_a_file_deleted_during_the_run_is_not_resurrected(tmp_path):
    """Recreating it would restore state its owner chose to drop — the same clobber."""
    f = _write(tmp_path / "active_transaction_tmux_6.json", "OLD")
    backup = _snapshot(f)
    f.unlink()

    assert _restore_untouched_transaction_files(backup) == [str(f)]
    assert not f.exists()


def test_an_empty_snapshot_reports_nothing():
    """No files, no noise — the report must mean something when it appears."""
    assert _restore_untouched_transaction_files({}) == []


@pytest.mark.parametrize("status", ["open", "closed"])
def test_the_rule_does_not_depend_on_what_the_file_says(tmp_path, status):
    """Ownership is decided by WHO WROTE LAST, not by the transaction's state.

    A closed transaction can be the correct current value (the practitioner just
    POSTFLIGHTed), and an open one can be stale. Reading the payload to decide
    would be an authority-on-the-wrong-field bug.
    """
    f = _write(tmp_path / "active_transaction_x11_1.json", "OLD", status="closed")
    backup = _snapshot(f)
    _write(f, "NEW", status=status)
    os.utime(f, ns=(0, os.stat(f).st_mtime_ns + 5_000_000))

    _restore_untouched_transaction_files(backup)
    assert json.loads(f.read_text())["transaction_id"] == "NEW"

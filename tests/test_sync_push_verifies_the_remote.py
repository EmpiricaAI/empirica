"""`git push` exiting 0 is not evidence that anything replicated.

A refspec that matches nothing local — `refs/notes/empirica/*` on a repo whose
notes live elsewhere, or a remote that silently accepts nothing — exits **zero**
and pushes **nothing**. From the exit code alone that is indistinguishable from
replicating the whole graph, and sync-push reported `ok: true` for both.

Measured consequence: two practices carried five-figure local-only ref counts
while every push had "succeeded" — 5,622 here, 3,617 on a peer's box, 69% of
their epistemic graph. Nobody had done anything wrong; the success signal was
answering a different question.

So success is now judged by whether the REMOTE MOVED, counted before and after
through the same counters `sync-status` uses.
"""

from __future__ import annotations

import pytest

import empirica.cli.command_handlers.sync_commands as sc


class _Args:
    output = "json"
    remote = None
    verbose = False


@pytest.fixture
def push_env(monkeypatch):
    """Neutralize everything except the verification arithmetic."""
    monkeypatch.setattr(sc, "_load_sync_config", lambda: {"remote": "forgejo", "notes_remote": "forgejo"})
    # NOT stubbing `_handle_sync_push_command_helper`: it is the printer, so
    # replacing it makes every assertion read an empty stdout and "fail" for a
    # reason that has nothing to do with the verification. The first cut of this
    # fixture did exactly that — a fake aimed at the wrong seam.

    class _Ok:
        returncode = 0
        stderr = ""
        stdout = ""

    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: _Ok())
    return monkeypatch


def _counts(monkeypatch, local, before, after, after_err=None):
    monkeypatch.setattr(sc, "_count_all_local_note_refs", lambda: local)
    seq = iter([(before, None), (after, after_err)])
    monkeypatch.setattr(sc, "_count_remote_notes", lambda *a, **k: next(seq))


def test_exit_zero_with_an_unmoved_remote_is_NOT_success(push_env, capsys):
    """THE regression. git returns 0, nothing replicated, and the old code
    reported ok: true — which is how 5,622 refs went missing in plain sight."""
    _counts(push_env, local=5622, before=0, after=0)

    rc = sc.handle_sync_push_command(_Args())

    assert rc == 1
    out = capsys.readouterr().out
    assert "not_replicating" in out
    assert "did not move" in out


def test_a_real_push_still_succeeds(push_env, capsys):
    """POSITIVE CONTROL, and the failure mode worse than the bug: a verification
    that never passes would make every healthy push report a disaster."""
    _counts(push_env, local=100, before=40, after=100)

    rc = sc.handle_sync_push_command(_Args())

    assert rc == 0
    assert "replicated" in capsys.readouterr().out


def test_a_partial_push_says_partial_with_the_magnitude(push_env, capsys):
    """The remote moved but not all the way. A boolean cannot say this, and the
    number is what tells an operator whether to investigate or re-run."""
    _counts(push_env, local=100, before=10, after=60)

    sc.handle_sync_push_command(_Args())
    out = capsys.readouterr().out

    assert "partial" in out
    assert '"missing": 40' in out


def test_an_unreachable_remote_after_the_push_reports_UNKNOWN(push_env, capsys):
    """Degrading to zero would report a healthy seat as catastrophic; degrading
    to silence is the original defect. Unknown, carrying the reason."""
    _counts(push_env, local=100, before=100, after=None, after_err="ssh: connection refused")

    sc.handle_sync_push_command(_Args())
    out = capsys.readouterr().out

    assert "unknown" in out
    assert "connection refused" in out


def test_nothing_to_push_is_not_a_failure(push_env, capsys):
    """A seat with zero local refs and zero remote refs has replicated
    everything it has. Reporting that as not_replicating would fire on every
    fresh practice — the over-firing shape that trains dismissal."""
    _counts(push_env, local=0, before=0, after=0)

    rc = sc.handle_sync_push_command(_Args())

    assert rc == 0
    assert "replicated" in capsys.readouterr().out


def test_the_verification_rides_on_the_response(push_env, capsys):
    """A verdict computed and not surfaced is the same silence with extra steps —
    this repo's recurring defect. It must reach the caller's JSON."""
    _counts(push_env, local=10, before=5, after=10)

    sc.handle_sync_push_command(_Args())
    out = capsys.readouterr().out

    for key in ("verification", "local_refs", "remote_before", "remote_after", "verdict"):
        assert key in out

"""Configuration is not replication, and `sync-status` could only answer the first.

Measured across four practices on 2026-09-02: **~9,500 epistemic artifact refs had
never left the machine they were written on**, and `sync-status` reported healthy for
every one. "A remote is configured" was the only question the verb could answer, so a
seat with a perfectly good remote and nothing on it looked identical to a seat that
was fully synced.

A verb someone consults to decide whether a thing is working must be able to say
*it is not*.

`local > remote` is the whole signature and it is cheap — two counts, no history walk.
The tests that matter here are the ones asserting the NEGATIVE states render, and the
one asserting both sides count the SAME ref set.
"""

from __future__ import annotations

from empirica.cli.command_handlers.sync_commands import _replication_verdict


def test_nothing_ever_pushed_is_named_as_such():
    """THE case. Three of the four measured seats were exactly this — a configured
    remote holding zero refs — and it rendered as healthy."""
    v = _replication_verdict(local=3617, remote_count=0, unreachable=None)

    assert v["state"] == "not_replicating"
    assert v["behind"] == 3617
    assert "nothing has ever been pushed" in v["reason"]


def test_partial_replication_is_behind_not_replicated():
    """The fourth seat. A remote with SOME refs is the easiest to misread as working,
    because every surface that checks presence finds something."""
    v = _replication_verdict(local=17637, remote_count=12017, unreachable=None)

    assert v["state"] == "behind"
    assert v["behind"] == 5620
    assert "5620 of 17637" in v["reason"], "the reader needs the magnitude, not a boolean"


def test_a_replicated_seat_says_so():
    """POSITIVE CONTROL. A verdict that only ever reported problems would satisfy the
    tests above while telling a healthy seat nothing — and a check that cries wolf gets
    silenced the first time it does."""
    v = _replication_verdict(local=500, remote_count=500, unreachable=None)

    assert v["state"] == "replicated"
    assert v["behind"] == 0


def test_a_remote_ahead_is_not_reported_as_behind():
    """A remote holding MORE than local (a peer pushed, we have not fetched) is not
    this defect. Reporting it as `behind` would send the reader to push over someone
    else's work."""
    v = _replication_verdict(local=100, remote_count=140, unreachable=None)

    assert v["state"] == "replicated"
    assert v["behind"] == 0


def test_an_unreachable_remote_is_unknown_never_zero_and_never_fine():
    """NEGATIVE CONTROL on the failure path, and the one that would hurt most.

    A network failure and an empty remote are OPPOSITE facts and only one is a sync
    problem. Degrading to 0 would report a healthy seat as catastrophically broken;
    degrading to silence would report a broken seat as healthy. Both are worse than
    saying the remote could not be reached.
    """
    v = _replication_verdict(local=500, remote_count=None, unreachable="timed out after 20s reaching forgejo")

    assert v["state"] == "unknown"
    assert v["behind"] is None
    assert "timed out" in v["reason"], "and it must carry WHY, not just that it failed"


def test_an_empty_local_graph_is_not_a_replication_failure():
    """A fresh practice has nothing to replicate. Reporting that as `not_replicating`
    is the cry-wolf case: the first thing a new seat would ever see."""
    v = _replication_verdict(local=0, remote_count=0, unreachable=None)

    assert v["state"] == "nothing_to_replicate"


def test_both_sides_must_count_the_same_ref_set():
    """THE bug caught by running it, and the direction of the error was reassuring.

    The first version compared `total_notes` — the seven enumerated namespaces — against
    the remote's `refs/notes/*` wholesale. On core's own repo that is 6,405 against
    12,017, so it printed `REPLICATED — all 6405 local note refs are on the remote`
    while 5,622 refs were in fact missing.

    Two counts over two different sets is not a comparison. This pins the property at
    the level where it broke: given the SAME set on both sides the verdict is right,
    and given the mismatched one it is not.
    """
    # Same set, genuinely behind → behind.
    assert _replication_verdict(17639, 12017, None)["state"] == "behind"
    # The mismatched pair the first version fed it. If a future refactor reintroduces
    # a subset on the local side, this is the shape it produces: a false all-clear.
    assert _replication_verdict(6405, 12017, None)["state"] == "replicated"

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

import json
import subprocess
from types import SimpleNamespace

import pytest

from empirica.cli.command_handlers import sync_commands
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


def test_a_tiny_gap_never_renders_as_zero_percent():
    """A real one-ref gap used to read `1 of 970 local note refs (0%) are NOT on the
    remote` — prose in contradiction with the categorical state beside it.

    The integer `behind` is what a consumer should threshold on and it was always
    right. The risk is a later "fix" that softens the STATE to match the number,
    repairing the honest half. `<1%` removes the contradiction instead.
    """
    v = _replication_verdict(local=970, remote_count=969, unreachable=None)

    assert v["state"] == "behind"
    assert v["behind"] == 1
    assert "(<1%)" in v["reason"]
    assert "(0%)" not in v["reason"]


def test_ordinary_percentages_still_round_normally():
    """POSITIVE CONTROL. A `<1` that leaked into every reason would make the magnitude
    useless — the thing this field exists to carry."""
    assert "(32%)" in _replication_verdict(17639, 12017, None)["reason"]
    assert "(100%)" in _replication_verdict(3617, 0, None)["reason"]


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


@pytest.mark.parametrize(
    ("config", "expect_in_reason"),
    [
        ("sync: {}\n", "no notes remote configured"),
        ("sync:\n  notes_remote: nosuch\n", "not a git remote here"),
    ],
    ids=["no-remote", "remote-not-in-repo"],
)
def test_the_replication_key_is_present_even_when_nothing_could_be_computed(
    tmp_path, monkeypatch, capsys, config, expect_in_reason
):
    """THE gap, reported by a peer against the first version.

    The key used to be OMITTED when no remote was configured — which is the default
    state of every seat after the no-default change, so it was the common case at
    rollout, not an edge. A consumer cannot tell an absent key apart from *computed
    and fine* or *this build predates the field*: absence defaults to whatever the
    reader assumes.

    Same collapse as PASS-vs-SKIP in doctor and unset-vs-misconfigured in this very
    verb — third instance in one command, one layer down, applied to a JSON key.
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "config.yaml").write_text(f'version: "2.0"\n{config}')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sync_commands, "_get_workspace_root", lambda: str(tmp_path))

    args = SimpleNamespace(remote=None, output="json", verbose=False, local=False)
    assert sync_commands.handle_sync_status_command(args) == 0
    result = json.loads(capsys.readouterr().out)

    assert "replication" in result, "the key must be present, not omitted"
    assert result["replication"]["state"] == "unknown"
    assert expect_in_reason in result["replication"]["reason"]
    assert result["replication"]["behind"] is None
    assert result["remote_notes"] is None


def test_local_flag_says_it_skipped_rather_than_reporting_nothing(tmp_path, monkeypatch, capsys):
    """`--local` is a deliberate opt-out of the network call, and it must SAY so.
    Silently returning no verdict would make the fast path indistinguishable from a
    healthy one — which is the defect this whole field exists to remove."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "forgejo", str(tmp_path / "x.git")], cwd=tmp_path, check=True, capture_output=True
    )
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "config.yaml").write_text('version: "2.0"\nsync:\n  notes_remote: forgejo\n')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sync_commands, "_get_workspace_root", lambda: str(tmp_path))

    args = SimpleNamespace(remote=None, output="json", verbose=False, local=True)
    assert sync_commands.handle_sync_status_command(args) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["replication"]["state"] == "unknown"
    assert "--local" in result["replication"]["reason"]


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

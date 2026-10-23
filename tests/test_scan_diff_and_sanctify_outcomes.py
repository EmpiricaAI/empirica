"""The scan diff must see processes and new listeners, and sanctify must count a retraction (goals c5a29f46 and 8104e065).

Found by the pilot sweep and reproduced on develop before any fix:

* ``scan diff`` grouped EVERY process under ``?``: ``_summarize_processes`` keys on ``name``/``comm``, which the default read
  surface never collects (a process row carries ``cmdline``, a redacted string). Two snapshots with different processes
  therefore reported ``changed: [{name: '?', ...}]`` and never ``added``/``removed``.
* ``scan diff`` never reported a new listener: ``network.listening_ports`` is a sorted list of INTS, and
  ``_summarize_listeners`` skipped everything that was not a dict, so the listener set was always empty and the services audit,
  which reads that diff, could not fire on a port that appeared.
* ``derive_standing`` scored a source whose artifact was RETRACTED as perfectly stable: ``retracted`` (the outcome
  ``resolve-artifacts`` records for a retraction kind) was in neither outcome set.

The fixtures have the shapes the collectors really emit, not the shapes the summarisers happened to expect.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers.scan_commands import _compute_scan_diff, _summarize_listeners, _summarize_processes
from empirica.core.sources.sanctify import derive_standing


def _snap(processes=None, ports=None):
    return {"snapshot": {"processes": processes or [], "network": {"listening_ports": ports or []}, "coverage": {}}}


def _proc(pid, cmdline):
    # the default read surface: no name, no comm
    return {"pid": pid, "cmdline": cmdline, "parent_pid": 1, "age_seconds": 10.0}


# --- processes ---------------------------------------------------------------------------------------------------------------


def test_processes_are_named_from_their_cmdline():
    counts = _summarize_processes(
        _snap([_proc(1, "/usr/sbin/sshd -D"), _proc(2, "/usr/sbin/sshd -D"), _proc(3, "/usr/bin/cron -f")])
    )
    assert counts == {"sshd": 2, "cron": 1}


def test_an_interpreter_is_named_by_the_script_it_runs():
    counts = _summarize_processes(
        _snap(
            [
                _proc(1, "/home/u/.venv/bin/python3.14 /home/u/.local/bin/empirica loop listen --instance a"),
                _proc(2, "python3 -m http.server 8767"),
                _proc(3, "/usr/bin/bash -c tail -F x"),
                _proc(4, "node /srv/app/server.js"),
            ]
        )
    )
    assert counts == {"python3.14 empirica": 1, "python3 http.server": 1, "bash -c": 1, "node server.js": 1}


def test_an_empty_or_missing_cmdline_is_still_counted_under_a_visible_name():
    counts = _summarize_processes(_snap([{"pid": 9}, _proc(10, ""), _proc(11, "   ")]))
    assert counts == {"?": 3}


def test_an_explicit_name_still_wins():
    assert _summarize_processes(_snap([{"pid": 1, "name": "nginx", "cmdline": "/x/y z"}])) == {"nginx": 1}


def test_a_new_process_is_reported_as_added_not_as_a_change_of_unknown():
    a = _snap([_proc(1, "/usr/bin/cron -f")])
    b = _snap([_proc(1, "/usr/bin/cron -f"), _proc(2, "/tmp/.x/miner --pool x")])
    diff = _compute_scan_diff(a, b)
    assert diff["processes"]["added"] == ["miner"]
    assert diff["processes"]["removed"] == [] and diff["processes"]["changed"] == []


def test_an_unchanged_scan_reports_no_process_change():
    a = _snap([_proc(1, "/usr/bin/cron -f"), _proc(2, "/usr/sbin/sshd -D")])
    diff = _compute_scan_diff(a, _snap([_proc(7, "/usr/bin/cron -f"), _proc(8, "/usr/sbin/sshd -D")]))
    assert diff["processes"] == {"added": [], "removed": [], "changed": []}, "pids move; names do not"


# --- listeners ---------------------------------------------------------------------------------------------------------------


def test_listening_ports_as_the_scanner_emits_them_are_summarised():
    assert _summarize_listeners(_snap(ports=[22, 8080])) == {"22", "8080"}


def test_a_new_listener_is_reported_as_added():
    diff = _compute_scan_diff(_snap(ports=[22, 8080]), _snap(ports=[22, 8080, 9000]))
    assert diff["listeners"]["added"] == ["9000"] and diff["listeners"]["removed"] == []


def test_a_closed_listener_is_reported_as_removed():
    diff = _compute_scan_diff(_snap(ports=[22, 8080]), _snap(ports=[22]))
    assert diff["listeners"]["removed"] == ["8080"] and diff["listeners"]["added"] == []


def test_dict_shaped_listeners_still_work_and_a_mixed_list_does_not_crash():
    out = _summarize_listeners(_snap(ports=[{"host": "127.0.0.1", "port": 22}, 8080, None, "x"]))
    assert "127.0.0.1:22" in out and "8080" in out


def test_identical_listeners_report_no_change():
    diff = _compute_scan_diff(_snap(ports=[22, 80]), _snap(ports=[80, 22]))
    assert diff["listeners"] == {"added": [], "removed": []}


# --- sanctify ----------------------------------------------------------------------------------------------------------------


def _standing(*outcomes, implicated=False):
    events = [{"event": "source_outcome", "outcome": o, "implicated": implicated} for o in outcomes]
    return derive_standing(events, citation_count=1, last_reviewed_at=None, now=0.0)


def test_a_retracted_artifact_lowers_the_sources_stability():
    standing = _standing("retracted")
    assert standing["stability"] == 0.0, "one outcome, and it moved: nothing here is stable"


def test_a_retraction_with_the_source_implicated_counts_against_accuracy():
    standing = _standing("confirmed", "retracted", implicated=True)
    assert standing["accuracy_basis"]["implicated_failures"] == 1
    assert standing["accuracy"] == pytest.approx(0.5)


def test_a_retraction_the_caller_did_not_implicate_does_not_slander_the_source():
    standing = _standing("retracted", implicated=False)
    assert standing["accuracy"] is None and standing["accuracy_basis"]["implicated_failures"] == 0
    assert standing["stability"] == 0.0, "stability is reported separately from accuracy"


def test_confirmations_still_read_as_stable():
    standing = _standing("confirmed", "confirmed")
    assert standing["stability"] == 1.0 and standing["accuracy"] == 1.0


def test_superseded_and_invalidated_still_move_stability():
    assert _standing("superseded", "invalidated", "confirmed", "confirmed")["stability"] == pytest.approx(0.5)


# --- a mistyped artifact is not a retraction ----------------------------------------------------------------------------------


def test_a_mistyped_artifact_moves_stability_but_never_counts_against_the_source():
    """`mistyped` means the artifact belonged to another type. The sources it cited were not wrong; scoring it like a retraction
    taught the standing that a misfiled but true finding had misled its source."""
    standing = _standing("confirmed", "mistyped", implicated=True)
    assert standing["stability"] == pytest.approx(0.5), "it moved, so stability falls"
    assert standing["accuracy_basis"]["implicated_failures"] == 0, "but nothing the source said was wrong"
    assert standing["accuracy"] == pytest.approx(1.0)


def test_a_retraction_still_counts_against_an_implicated_source():
    """Control: the same shape with `retracted` does count."""
    assert _standing("confirmed", "retracted", implicated=True)["accuracy_basis"]["implicated_failures"] == 1

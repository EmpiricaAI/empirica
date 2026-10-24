"""wakeable_panes maps a practice to the tmux panes a wake may target (goal 87772ac4).

A wake that reaches the wrong practice's pane gives that session a turn it did not ask for, and one that misses a pane leaves a
practice unheard. The sources (named seats, unbound panes, the instance-to-practice binding) are injected, so no test depends on
tmux, psutil or what happens to be running on the box.
"""

from __future__ import annotations

from empirica.core.cockpit.wake_targets import wakeable_panes

BINDING = {
    "seat-a": "empirica",
    "seat-b": "empirica",
    "seat-c": "empirica-cortex",
    "tmux_9": "empirica",
    "tmux_8": "other",
}


def _ai(instance_id):
    return BINDING.get(instance_id)


def test_named_seats_of_the_practice_are_found_by_their_binding():
    seats = {"seat-a": "0", "seat-b": "2", "seat-c": "1"}
    assert wakeable_panes("empirica", seat_panes=lambda: seats, unbound_panes=lambda: [], ai_of=_ai) == [
        "tmux_0",
        "tmux_2",
    ]


def test_unbound_panes_are_matched_through_their_tmux_id():
    out = wakeable_panes("empirica", seat_panes=lambda: {}, unbound_panes=lambda: ["8", "9"], ai_of=_ai)
    assert out == ["tmux_9"], "pane 8 is bound to another practice and must not be woken"


def test_a_seat_with_no_project_binding_counts_as_its_own_practice():
    assert wakeable_panes(
        "lone-seat", seat_panes=lambda: {"lone-seat": "6"}, unbound_panes=lambda: [], ai_of=lambda _i: None
    ) == ["tmux_6"]


def test_another_practice_gets_nothing_of_ours():
    assert wakeable_panes("nobody", seat_panes=lambda: {"seat-a": "0"}, unbound_panes=lambda: ["9"], ai_of=_ai) == []


def test_a_pane_reached_both_ways_is_listed_once():
    out = wakeable_panes("empirica", seat_panes=lambda: {"seat-a": "9"}, unbound_panes=lambda: ["9"], ai_of=_ai)
    assert out == ["tmux_9"]


def test_a_failing_source_returns_what_was_found_and_never_raises():
    def broken():
        raise OSError("tmux gone")

    assert wakeable_panes("empirica", seat_panes=lambda: {"seat-a": "0"}, unbound_panes=broken, ai_of=_ai) == ["tmux_0"]
    assert wakeable_panes("empirica", seat_panes=broken, unbound_panes=lambda: [], ai_of=_ai) == []

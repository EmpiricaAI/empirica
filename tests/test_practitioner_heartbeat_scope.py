"""Each listener forwards ITS OWN practice's presence, not the whole box.

The presence store is box-global (`~/.empirica/practitioner_presence_*.json`),
and `list_presence` has taken a `practice_ai_id` scope since it was written — its
own docstring calls it "practice → its active practitioner(s)". This emitter
never passed it. So each listener read the whole store and posted EVERY
practitioner on the machine, making the emission rate `listeners x sessions`
rather than `sessions`, with every post but one a duplicate upsert of a row
another listener had just written (cortex keys on user_id x machine x session_id).

**The capability existed; the caller was the defect.** My first fix added a
parallel filter in the emitter, which is a second implementation of scoping that
already worked — found only because David asked where the unlabelled records
were.

Measured by mesh-support 2026-08-05: **12,272 req/hr against an expected 540**
from a 60s interval — a 22.7x gap they read as "the interval is not honoured".
It was honoured, nine times over. Confirmed here: 4 live records x 9 listeners,
and their 3.41/s needs `listeners x sessions ~= 204`, which is 9 x ~23 sessions.

The duplication is invisible to a per-session query, because the duplicate rows
are identical — which is why the discriminator had to come from the code.
"""

from __future__ import annotations

from empirica.core.loop_scheduler.practitioner_heartbeat import PractitionerHeartbeatEmitter


def test_default_list_passes_its_scope_to_list_presence(monkeypatch):
    """The whole fix: the emitter asks list_presence for ITS practice."""
    seen = {}

    def fake_list_presence(practice_ai_id=None, *, include_stale=False, **kw):
        seen["practice_ai_id"] = practice_ai_id
        seen["include_stale"] = include_stale
        return []

    import empirica.core.practitioner_presence as pp

    monkeypatch.setattr(pp, "list_presence", fake_list_presence)
    PractitionerHeartbeatEmitter(ai_id="empirica")._default_list()
    assert seen == {"practice_ai_id": "empirica", "include_stale": False}


def test_no_ai_id_reads_the_whole_box(monkeypatch):
    """Box-level aggregation stays available — but as an explicit opt-in rather
    than the accidental default it used to be. `None` is list_presence's own
    "no scope" value, so this is the pre-fix behaviour, deliberately reachable."""
    seen = {}

    def fake_list_presence(practice_ai_id=None, **kw):
        seen["practice_ai_id"] = practice_ai_id
        return []

    import empirica.core.practitioner_presence as pp

    monkeypatch.setattr(pp, "list_presence", fake_list_presence)
    PractitionerHeartbeatEmitter()._default_list()
    assert seen["practice_ai_id"] is None
    PractitionerHeartbeatEmitter(ai_id="   ")._default_list()
    assert seen["practice_ai_id"] is None


def test_scoping_lives_in_list_presence_not_in_a_second_filter():
    """Guard against re-adding the parallel filter I wrote first.

    `list_presence` already scopes on `practice_ai_id`; a second filter inside the
    emitter is a duplicate implementation that can drift from it — and mine did,
    by keeping unlabelled records that cannot exist (`practice_ai_id` is a
    required positional at write time; measured 24 live records, 0 unlabelled)."""
    import inspect

    from empirica.core.loop_scheduler import practitioner_heartbeat as m

    assert not hasattr(m.PractitionerHeartbeatEmitter, "_scoped"), (
        "scoping belongs to list_presence, which has taken a practice_ai_id "
        "argument since it was written — the emitter's job is to pass it"
    )
    src = inspect.getsource(m.PractitionerHeartbeatEmitter._default_list)
    assert "list_presence(self.ai_id" in src


def test_listener_constructs_the_emitter_scoped():
    """The fix is only live if the caller passes it — an optional parameter nobody
    supplies is a fix in name."""
    from pathlib import Path

    src = (Path(__file__).parent.parent / "empirica" / "core" / "loop_scheduler" / "listener.py").read_text()
    assert "PractitionerHeartbeatEmitter(ai_id=instance_id)" in src, (
        "the listener must scope its practitioner emitter to its own ai_id, or every "
        "listener keeps forwarding every practitioner on the box"
    )

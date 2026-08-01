"""No cron loop is ever installed by default.

Wake-on-event is the preferred trigger wherever it is possible; a cron job is a
standing scheduled process on someone's machine and must be asked for (David,
2026-08-01: "cronjobs should be opt in only, never installed by default — we
always want wake on event if possible").

`cortex-mailbox-poll` already carried `opt_in_only` from the earlier version of
this rule. `message-cleanup` did not — it is `kind="cron"` and auto-installed,
with a comment explicitly exempting "genuine housekeeping crons" from the
carve-out. So the entry that broke the rule was the one that never opted into the
flag guarding it.

That is why the gate keys on `kind` rather than on the flag: a cron loop added
later cannot auto-install by forgetting to set something. Same shape as the
id-prefix guards elsewhere in this codebase — guard the class, not the instance,
because the instance is what gets forgotten.
"""

from __future__ import annotations

import pytest

from empirica.core.cockpit.canonical_loops import CANONICAL_LOOPS


def test_every_cron_loop_in_the_catalogue_is_opt_in():
    """POSITIVE CONTROL — message-cleanup failed this."""
    offenders = [e["name"] for e in CANONICAL_LOOPS if e.get("kind") == "cron" and not e.get("opt_in_only")]

    assert offenders == [], f"cron loops missing opt_in_only: {offenders}"


def test_the_catalogue_still_contains_a_cron_loop():
    """NEGATIVE CONTROL: the test above passes trivially if cron loops are all
    deleted. Something must still be under test."""
    assert any(e.get("kind") == "cron" for e in CANONICAL_LOOPS)


@pytest.fixture
def fresh_practice(tmp_path, monkeypatch):
    """A project that passes every gate up to the per-loop filter, so what the
    filter does is the only thing under test."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "proj" / ".empirica").mkdir(parents=True)

    import empirica.core.cockpit.canonical_loops as cl

    queued: list[str] = []

    def _fake_write_pending(**kw):
        queued.append(kw["name"])

    monkeypatch.setattr("empirica.core.cockpit.loop_install_request.write_pending", _fake_write_pending)

    class _EmptyRegistry:
        def __init__(self, *a, **k):
            pass

        def list_loops(self):
            return []

    monkeypatch.setattr("empirica.core.cockpit.loop_registry.LoopRegistry", _EmptyRegistry)
    return cl, tmp_path / "proj", queued


def test_no_cron_loop_is_queued_on_a_fresh_practice(fresh_practice):
    """The behaviour that matters: a brand-new practice must not acquire a
    scheduled job it never asked for."""
    cl, project_root, queued = fresh_practice

    cl.maybe_queue_canonical_install("seat-1", project_root, "test")

    cron_names = {e["name"] for e in CANONICAL_LOOPS if e.get("kind") == "cron"}
    assert not (cron_names & set(queued)), f"a cron loop was auto-installed: {cron_names & set(queued)}"


def test_the_gate_survives_a_dropped_flag(fresh_practice, monkeypatch):
    """The structural claim. Simulate someone adding a cron loop and forgetting
    `opt_in_only` — exactly how message-cleanup slipped through. It must still
    not install."""
    cl, project_root, queued = fresh_practice
    forgetful = {
        "name": "forgot-the-flag",
        "kind": "cron",
        "cron": "0 4 * * *",
        "description": "a cron loop whose author did not set opt_in_only",
        "body_skill": "noop",
    }
    monkeypatch.setattr(cl, "CANONICAL_LOOPS", [*CANONICAL_LOOPS, forgetful])

    cl.maybe_queue_canonical_install("seat-1", project_root, "test")

    assert "forgot-the-flag" not in queued


def test_a_non_cron_auto_installable_loop_would_still_queue(fresh_practice, monkeypatch):
    """NEGATIVE CONTROL: gating on kind must not disable auto-install wholesale.
    An interval loop without opt_in_only is still installed."""
    cl, project_root, queued = fresh_practice
    ordinary = {
        "name": "ordinary-interval-loop",
        "kind": "interval",
        "interval": "5m",
        "description": "not cron, not opt-in-only",
        "body_skill": "noop",
    }
    monkeypatch.setattr(cl, "CANONICAL_LOOPS", [*CANONICAL_LOOPS, ordinary])

    cl.maybe_queue_canonical_install("seat-1", project_root, "test")

    assert "ordinary-interval-loop" in queued


def test_the_wake_on_event_loop_is_still_opt_in(fresh_practice):
    """Regression guard on the earlier rule — cortex-mailbox-poll must not
    start auto-installing as a side effect of this change."""
    cl, project_root, queued = fresh_practice

    cl.maybe_queue_canonical_install("seat-1", project_root, "test")

    assert "cortex-mailbox-poll" not in queued

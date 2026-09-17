"""Staleness must reach something other than a TUI.

`_loop_is_stale` existed, worked, and its only two call sites were both in the
render layer. So it drew a warning into a terminal nobody was watching while
`amex-belege` sat at **70x its interval for ten weeks**.

It is the sibling of the pause-guard defect (fixed `1761911a7`): `is_loop_paused`
was display-only and nothing ENFORCED it; this was display-only and nothing
REPORTED it. A control that only prints, and an alarm that only draws.

Two halves shipped together, and the second is the one with teeth:

  * the detector moved from `core/cockpit/render.py` to `core/cockpit/loop_registry.py`
    — state computation belongs with the state. A non-visual consumer importing
    staleness from a render module is the dependency pointing the wrong way, and
    re-deriving the rule beside it would be two sources of truth for one question.
  * `doctor` now has `check_loops_not_stale`, so the signal has a non-visual reader.

Verified live on this box before shipping: WARN, 3 of 17 loops past interval,
worst at 2,998h against a 30s interval. Running it mattered — the first version
returned SKIP from a wrong import and would have shipped as a permanent no-op,
which is the same defect class one layer out.
"""

from __future__ import annotations

import json

import pytest

from empirica.cli.command_handlers.diagnose import PASS, SKIP, WARN, check_loops_not_stale
from empirica.core.cockpit.loop_registry import interval_to_seconds, loop_is_stale


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(tz=timezone.utc).isoformat()


def _registry(tmp_path, monkeypatch, loops: dict, name: str = "loops_probe.json"):
    """Point the check at a throwaway registry dir, never the real ~/.empirica."""
    (tmp_path / name).write_text(json.dumps({"instance_id": "probe", "loops": loops}))
    monkeypatch.setattr("empirica.core.cockpit.loop_registry.EMPIRICA_DIR", tmp_path)
    return tmp_path


# ─── the detector, now in a non-visual home ───────────────────────────


def test_the_detector_lives_with_the_state_not_the_rendering():
    """The move IS the fix's first half — assert it, or it drifts back."""
    assert loop_is_stale.__module__ == "empirica.core.cockpit.loop_registry"


def test_render_still_uses_the_same_function_object():
    """No second copy. Two implementations of one rule is the worse outcome."""
    from empirica.core.cockpit import render

    assert render._loop_is_stale is loop_is_stale


@pytest.mark.parametrize(
    ("interval", "expected"),
    [("30s", 30.0), ("5m", 300.0), ("2h", 7200.0), ("1d", 86_400.0), ("15", 900.0), ("", None), (None, None)],
)
def test_interval_parsing_survived_the_move(interval, expected):
    assert interval_to_seconds(interval) == expected


def test_a_loop_that_never_ran_is_not_stale():
    """Never-run and overdue are different states.

    Conflating them would fire on every freshly-registered loop and train the
    reader to ignore the warning.
    """
    assert loop_is_stale({"interval": "5m"}) is False


def test_a_loop_with_no_interval_is_not_stale():
    assert loop_is_stale({"last_run": "2020-01-01T00:00:00Z"}) is False


# ─── the non-visual consumer ──────────────────────────────────────────


def test_doctor_warns_on_an_overdue_loop(tmp_path, monkeypatch):
    """The defect, as an assertion: the signal now reaches a non-TUI reader."""
    _registry(tmp_path, monkeypatch, {"amex-belege": {"interval": "30s", "last_run": "2020-01-01T00:00:00Z"}})

    r = check_loops_not_stale()

    assert r.status == WARN
    assert "amex-belege" in r.detail, "the detail must name the loop, not just count it"
    assert r.data["stale"][0]["hours_since_last_run"] > 0
    assert r.hint, "a WARN with no hint leaves the reader nowhere to go"


def test_doctor_passes_when_every_loop_is_current(tmp_path, monkeypatch):
    """Positive control.

    Without it, a check that always WARNed would satisfy the test above while
    making every healthy box look broken — and a warning that always fires is a
    warning nobody reads.
    """
    from datetime import datetime, timezone

    now = datetime.now(tz=timezone.utc).isoformat()
    _registry(tmp_path, monkeypatch, {"fresh": {"interval": "1d", "last_run": now}})

    r = check_loops_not_stale()

    assert r.status == PASS
    assert "1 loop" in r.detail


def test_a_paused_loop_is_not_reported(tmp_path, monkeypatch):
    """Paused is intentional; only neglect is the signal.

    Reporting deliberately-paused loops would bury the one that is actually
    broken under ones somebody switched off on purpose.
    """
    _registry(
        tmp_path,
        monkeypatch,
        {"off": {"interval": "30s", "last_run": "2020-01-01T00:00:00Z", "paused": True}},
    )

    assert check_loops_not_stale().status == PASS


def test_no_registry_skips_rather_than_passing(tmp_path, monkeypatch):
    """ "No registries found" is not "no stale loops".

    A PASS here would be a verdict over an empty set reading as a clean bill —
    the shape this whole family of fixes is about.
    """
    monkeypatch.setattr("empirica.core.cockpit.loop_registry.EMPIRICA_DIR", tmp_path)

    r = check_loops_not_stale()

    assert r.status == SKIP
    assert "no loop registries" in r.detail


def test_a_malformed_registry_does_not_hide_a_stale_loop_elsewhere(tmp_path, monkeypatch):
    """One unreadable file must not take the whole check down.

    Registries are per-instance, so a corrupt one should cost its own loops and
    nothing else. Returning SKIP for the lot would let one bad file silence every
    instance.
    """
    (tmp_path / "loops_broken.json").write_text("{not json")
    _registry(tmp_path, monkeypatch, {"late": {"interval": "30s", "last_run": "2020-01-01T00:00:00Z"}})

    r = check_loops_not_stale()

    assert r.status == WARN
    assert "late" in r.detail


def test_an_unreadable_registry_is_REPORTED_not_skipped(tmp_path, monkeypatch):
    """Found by a broccoli sweep of my own commit, an hour after shipping it.

    The first version wrote `continue` with the comment "a malformed registry is
    the cockpit's problem, not this check's". That is the rationalisation rather
    than the reasoning: whose problem the corruption is has nothing to do with
    whether this check may report a verdict over fewer registries than exist
    without saying so.

    It is the exemption-reports-clean-forever shape. The loops inside an unreadable
    file can never be found stale, so nothing distinguishes "no stale loops" from
    "did not look".
    """
    (tmp_path / "loops_broken.json").write_text("{not json")
    _registry(tmp_path, monkeypatch, {"fine": {"interval": "1d", "last_run": _now()}})

    r = check_loops_not_stale()

    assert r.status == WARN, "an unexamined registry cannot yield a clean PASS"
    assert "unreadable" in r.detail
    assert "broken" in r.detail, "name it — an unnamed count cannot be acted on"
    assert r.data["unreadable"] == ["broken"]
    assert r.hint


def test_no_unreadable_registries_means_no_caveat(tmp_path, monkeypatch):
    """Positive control for the report above.

    An unconditional caveat would satisfy it while making every healthy box carry
    a warning about a file that reads fine.
    """
    _registry(tmp_path, monkeypatch, {"fine": {"interval": "1d", "last_run": _now()}})

    r = check_loops_not_stale()

    assert r.status == PASS
    assert "unreadable" not in r.detail
    assert r.data["unreadable"] == []


def test_the_check_is_wired_into_the_run(tmp_path, monkeypatch):
    """A check nobody calls is the defect it was written to fix.

    `_loop_is_stale` was not broken — it was unreachable. Shipping this check
    without registering it would reproduce that exactly.
    """
    import inspect

    from empirica.cli.command_handlers import diagnose

    src = inspect.getsource(diagnose.run_all_checks) if hasattr(diagnose, "run_all_checks") else ""
    if not src:
        src = inspect.getsource(diagnose)
    assert "check_loops_not_stale()" in src, "the check must be appended to the diagnose run"

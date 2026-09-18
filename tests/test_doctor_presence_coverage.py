"""doctor's presence-coverage check: live practitioners whose practice has no
running listener go dark on a practice-scoped mesh. Shipped in a78883f41 with
no tests; these pin the four verdicts, in particular that a box where listener
units cannot be enumerated reports SKIP — the comparison was not made, so it
did not pass. Everything is injected; the box's real presence store and
systemd are never consulted.
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers import doctor as doctor_mod

UNITS = """\
  UNIT                                     LOAD   ACTIVE SUB     DESCRIPTION
  empirica-listener-empirica.service       loaded active running Empirica listener
  empirica-listener-empirica-cortex.service loaded active running Empirica listener
  other.service                            loaded active running Something else
"""


@pytest.fixture
def presence(monkeypatch):
    """Inject the live presence list and the systemctl answer."""
    state = {"live": [], "systemctl": (0, UNITS, "")}

    import types

    fake_pp = types.SimpleNamespace(list_presence=lambda include_stale=False: list(state["live"]))
    monkeypatch.setitem(__import__("sys").modules, "empirica.core.practitioner_presence", fake_pp)

    def run(cmd, timeout=None, **kw):
        if cmd[:2] == ["systemctl", "--user"]:
            return state["systemctl"]
        raise AssertionError(f"unexpected command {cmd}")

    monkeypatch.setattr(doctor_mod, "_run", run)
    return state


def test_no_live_practitioners_passes(presence):
    c = doctor_mod.check_orphaned_presence()
    assert c.status == doctor_mod.PASS
    assert "no live" in c.detail


def test_every_live_practice_has_a_listener(presence):
    presence["live"] = [{"practice_ai_id": "empirica"}, {"practice_ai_id": "empirica-cortex"}]
    c = doctor_mod.check_orphaned_presence()
    assert c.status == doctor_mod.PASS
    assert "2 live" in c.detail


def test_live_practice_without_listener_warns_and_names_it(presence):
    presence["live"] = [{"practice_ai_id": "empirica"}, {"practice_ai_id": "empirica-outreach"}]
    c = doctor_mod.check_orphaned_presence()
    assert c.status == doctor_mod.WARN
    assert "1 of 2" in c.detail
    assert "empirica-outreach=1" in c.detail
    assert c.data["orphans"] == {"empirica-outreach": 1}


def test_label_drift_is_an_orphan(presence):
    """`workspace` written by the record, `empirica-workspace` run by the listener."""
    presence["systemctl"] = (0, "  empirica-listener-empirica-workspace.service loaded active running x\n", "")
    presence["live"] = [{"practice_ai_id": "workspace"}]
    c = doctor_mod.check_orphaned_presence()
    assert c.status == doctor_mod.WARN
    assert "workspace=1" in c.detail


def test_units_not_enumerable_is_skip_not_pass(presence):
    """No user systemd (macOS, a container): the comparison cannot be made.
    The old verdict was PASS with a parenthetical, which folded 'not checked'
    into the pass count on exactly the boxes most likely to lack a listener."""
    presence["live"] = [{"practice_ai_id": "empirica"}]
    presence["systemctl"] = (1, "", "Failed to connect to bus")
    c = doctor_mod.check_orphaned_presence()
    assert c.status == doctor_mod.SKIP
    assert "NOT checked" in c.detail
    assert c.data == {"live": 1}


def test_unreadable_presence_store_warns(monkeypatch):
    import sys
    import types

    def boom(include_stale=False):
        raise OSError("store locked")

    monkeypatch.setitem(sys.modules, "empirica.core.practitioner_presence", types.SimpleNamespace(list_presence=boom))
    c = doctor_mod.check_orphaned_presence()
    assert c.status == doctor_mod.WARN
    assert "store locked" in c.hint

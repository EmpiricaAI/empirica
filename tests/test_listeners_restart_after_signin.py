"""A running listener picks up a new sign-in (goal 489c7fa0, prop_z2qcr27 #1 and #2).

A listener reads its ntfy url, token and topic once at start; nothing restarted it after `auth login` or `mesh connect`, so a
healthy one kept the old ones for good. Both now restart the installed listener services that are running (a stopped one
stays stopped), and say so when none is installed.
"""

from __future__ import annotations

from empirica.core.loop_scheduler import persistent_listener as pl


class _Svc:
    def __init__(self):
        self.restarted = []

    def restart_if_running(self, ai_id):
        self.restarted.append(ai_id)


def test_installed_ids_come_from_both_backends(tmp_path):
    (tmp_path / ".config/systemd/user").mkdir(parents=True)
    (tmp_path / ".config/systemd/user/empirica-listener-empirica.service").write_text("")
    (tmp_path / "Library/LaunchAgents").mkdir(parents=True)
    (tmp_path / "Library/LaunchAgents/com.empirica.listener.cortex.plist").write_text("")
    assert pl.installed_listener_ids(tmp_path) == {"empirica", "cortex"}


def test_restart_after_signin_restarts_each_installed_listener(tmp_path):
    (tmp_path / ".config/systemd/user").mkdir(parents=True)
    for ai in ("a", "b"):
        (tmp_path / f".config/systemd/user/empirica-listener-{ai}.service").write_text("")
    svc = _Svc()
    out = pl.restart_after_signin(service=svc, home=tmp_path)
    assert sorted(svc.restarted) == ["a", "b"] and out["restarted"] == ["a", "b"]


def test_none_installed_says_how_to_turn_one_on(tmp_path):
    out = pl.restart_after_signin(service=_Svc(), home=tmp_path)
    assert out["restarted"] == [] and "empirica listener on" in out["note"]


def test_restart_if_running_uses_try_restart(monkeypatch):
    calls = []
    monkeypatch.setattr(pl, "_systemctl", lambda *a, **k: calls.append(a))
    svc = pl.PersistentListenerService.__new__(pl.PersistentListenerService)
    svc.backend = "systemd"
    svc.restart_if_running("empirica")
    assert calls == [("try-restart", "empirica-listener-empirica.service")]

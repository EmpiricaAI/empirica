"""The cockpit bar on small clients and failed commands (goal 8da5f220 gap check): a menu or popup tmux cannot draw used to
show nothing, and an update window closed after 10 s whatever its exit status."""

from __future__ import annotations

from types import SimpleNamespace

from empirica.core.cockpit import bar


def test_the_menu_is_guarded_by_the_clients_height():
    args = bar.guarded_menu("P")
    assert args[:2] == ["if-shell", "-F"] and "client_height" in args[2]
    assert "display-menu" in args[3] and "too small for the command menu" in args[4]
    assert int(args[2].rsplit(",", 1)[1].rstrip("}")) >= bar.menu_rows(bar.menu_args("P"))


def test_the_help_popup_fits_a_narrow_client_and_says_when_it_cannot(monkeypatch):
    calls, said = [], []

    def fake_tmux(*args):
        calls.append(args)
        if args[0] == "display-message" and "-p" in args:
            return SimpleNamespace(stdout="80\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=1 if args[0] == "display-popup" else 0)

    monkeypatch.setattr(bar, "_tmux", fake_tmux)
    monkeypatch.setattr(bar, "_say", lambda client, text, ms=6000: said.append(text))
    bar._popup("c1", "help")
    popup = next(c for c in calls if c[0] == "display-popup")
    assert popup[popup.index("-w") + 1] == "78"
    assert said and "too small for the help" in said[0]


def test_a_failed_window_command_stays_on_screen(monkeypatch):
    calls = []
    monkeypatch.setattr(bar, "_tmux", lambda *a: calls.append(a) or SimpleNamespace(stdout="", returncode=0))
    bar._window("s", "update", "false")
    shown = calls[-1][-1]
    assert "FAILED" in shown and "read -r" in shown and "rc -eq 0" in shown

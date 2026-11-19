"""A cockpit config's `socket:` aims every tmux call at that server (goal 44203f3d, mesh-support prop_bvovvosl)."""

from __future__ import annotations

import re
from pathlib import Path

from empirica.core.cockpit import tmux_socket as ts

_ROOT = Path(__file__).resolve().parent.parent


def test_without_a_socket_tmux_is_plain(monkeypatch):
    monkeypatch.delenv(ts.ENV, raising=False)
    assert ts.tmux_argv("list-panes", "-a") == ["tmux", "list-panes", "-a"]


def test_with_a_socket_every_call_passes_it(monkeypatch):
    monkeypatch.setenv(ts.ENV, "/srv/cockpit/david.sock")
    assert ts.tmux_argv("attach", "-t", "c") == ["tmux", "-S", "/srv/cockpit/david.sock", "attach", "-t", "c"]


def test_the_config_reads_socket_group_and_access(tmp_path):
    from empirica.core.cockpit.launcher.config import load_config

    f = tmp_path / "c.yaml"
    f.write_text("socket: /srv/cockpit/d.sock\nserver_access: [cockpit]\nprojects: []\n")
    cfg = load_config(f)
    assert (cfg.socket, cfg.socket_group, cfg.server_access) == ("/srv/cockpit/d.sock", "cockpit-users", ["cockpit"])
    f.write_text("projects: []\n")
    assert load_config(f).socket is None


def test_no_cockpit_tmux_call_bypasses_the_socket():
    """Every tmux subprocess in the cockpit builds its argv through tmux_argv, so none reaches the default server."""
    stray = []
    for path in [*(_ROOT / "empirica" / "core" / "cockpit").rglob("*.py"),
                 _ROOT / "empirica" / "cli" / "command_handlers" / "cockpit_launcher_commands.py"]:  # fmt: skip
        if path.name == "tmux_socket.py":
            continue
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"""\[\s*["']tmux["']\s*,""", line) and "tmux_argv" not in line:
                stray.append(f"{path.relative_to(_ROOT)}:{n}")
    assert stray == []


def test_share_socket_opens_the_socket_to_the_group_and_grants_read_only_access(tmp_path, monkeypatch):
    import grp
    import os
    from types import SimpleNamespace

    from empirica.core.cockpit.launcher import tmux as lt

    sock = tmp_path / "d.sock"
    sock.write_text("")
    os.chmod(sock, 0o600)
    calls = []
    monkeypatch.setenv(ts.ENV, str(sock))
    group = grp.getgrgid(os.getgid()).gr_name  # a group this user is in, so chown works without privileges
    cfg = SimpleNamespace(socket=str(sock), socket_group=group, server_access=["cockpit"])
    out = lt.share_socket(cfg, run=lambda argv, **k: calls.append(argv) or SimpleNamespace(returncode=0, stderr=""))
    assert oct(sock.stat().st_mode & 0o777) == "0o660" and out["errors"] == []
    assert ["tmux", "-S", str(sock), "server-access", "-a", "cockpit", "-r"] in calls
    assert ["tmux", "-S", str(sock), "set-environment", "-g", "EMPIRICA_TMUX_SOCKET", str(sock)] in calls
    assert lt.share_socket(SimpleNamespace(socket=None)) is None

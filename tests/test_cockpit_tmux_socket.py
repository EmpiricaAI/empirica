"""A cockpit config's `socket:` aims every tmux call at that server (goal 44203f3d, mesh-support prop_bvovvosl)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

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
    assert ["tmux", "-S", str(sock), "server-access", "-a", "-r", "cockpit"] in calls  # flags before the user
    assert ["tmux", "-S", str(sock), "set-environment", "-g", "EMPIRICA_TMUX_SOCKET", str(sock)] in calls
    assert lt.share_socket(SimpleNamespace(socket=None)) is None


# ─── 1.17.11 sweep ──────────────────────────────────────────────────────────


def _socket_config(tmp_path, socket="/srv/cockpit/d.sock"):
    from empirica.core.cockpit.launcher.config import load_config

    f = tmp_path / "c.yaml"
    f.write_text(f"socket: {socket}\nprojects:\n  - name: p\n    path: {tmp_path}\n")
    return load_config(f)


def test_a_relaunch_asks_the_socket_server_whether_the_session_exists(tmp_path, monkeypatch):
    from empirica.core.cockpit.launcher import tmux as lt

    monkeypatch.setenv(ts.ENV, "placeholder")  # restored after the test; launch sets it from the config
    seen = []
    monkeypatch.setattr(lt, "tmux_available", lambda: True)
    monkeypatch.setattr(lt, "cockpit_session_exists", lambda name: seen.append(ts.socket_path()) or True)
    monkeypatch.setattr(lt, "strip_session_identity", lambda name: None)
    lt.launch_cockpit(_socket_config(tmp_path))
    assert seen == ["/srv/cockpit/d.sock"]


def test_each_socket_gets_its_own_server_unit(monkeypatch):
    from empirica.core.cockpit.launcher import tmux as lt

    monkeypatch.delenv(ts.ENV, raising=False)
    default = lt.server_unit()
    monkeypatch.setenv(ts.ENV, "/srv/cockpit/a.sock")
    a = lt.server_unit()
    monkeypatch.setenv(ts.ENV, "/srv/cockpit/b.sock")
    assert default == "empirica-tmux" and a.startswith("empirica-tmux-") and a != lt.server_unit()


def test_a_socket_that_does_not_exist_yet_is_named(tmp_path):
    from types import SimpleNamespace

    from empirica.core.cockpit.launcher import tmux as lt

    cfg = SimpleNamespace(socket=str(tmp_path / "none.sock"), socket_group="cockpit-users", server_access=[])
    assert "does not exist" in lt.share_socket(cfg)["errors"][0]


def test_sharing_errors_are_printed_even_for_an_existing_server(capsys):
    from empirica.cli.command_handlers.cockpit_launcher_commands import _say_tmux_server

    _say_tmux_server({"mode": "existing", "socket": {"errors": ["permissions: group 'cockpit-users' not found"]}})
    assert "cockpit-users" in capsys.readouterr().out


def test_the_socket_path_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert _socket_config(tmp_path, "~/c.sock").socket == str(tmp_path / "c.sock")


def test_every_cockpit_command_aims_at_the_configured_socket():
    """refresh, status, kill and launch load the config and must aim tmux at its socket before any call."""
    lines = (_ROOT / "empirica/cli/command_handlers/cockpit_launcher_commands.py").read_text().splitlines()
    loads = [i for i, line in enumerate(lines) if "config = load_config(" in line]
    assert len(loads) >= 4 and all("use_socket(" in lines[i + 1] for i in loads)


@pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")
def test_server_access_is_accepted_by_real_tmux(tmp_path, monkeypatch):
    """prop_nus3q64yzvg5xets3zrcvmfhgq: tmux 3.5+ takes the flags before the user; 'server-access -a USER -r' failed with
    'too many arguments' and every grant was silently dropped. Runs the real tmux on a server under tmp_path."""
    import getpass
    import grp
    import os
    import subprocess
    from types import SimpleNamespace

    from empirica.core.cockpit.launcher import tmux as lt

    sock = tmp_path / "s"
    subprocess.run(["tmux", "-S", str(sock), "new-session", "-d", "-s", "t"], check=True, timeout=20)
    try:
        monkeypatch.setenv(ts.ENV, str(sock))
        group = grp.getgrgid(os.getgid()).gr_name
        out = lt.share_socket(SimpleNamespace(socket=str(sock), socket_group=group, server_access=[getpass.getuser()]))
        assert not any("too many arguments" in e for e in out["errors"]), out["errors"]
    finally:
        subprocess.run(["tmux", "-S", str(sock), "kill-server"], check=False, timeout=20)

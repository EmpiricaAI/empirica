"""The relayout hooks, run by a real tmux: a burst coalesces, the last event is applied, and no pane opens view-mode.

The previous round checked the hook's text and ran it under /bin/sh, never under tmux, which is how a ``flock -n`` that
exits 1 shipped: tmux's run-shell shows a failed command in view-mode on the active pane.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from empirica.core.cockpit import bar

pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="needs tmux")

# A directory name with a space, a dollar, both quotes and a hash: everything tmux's double quotes, its format expansion and
# /bin/sh could each get wrong.
AWKWARD = "st ate $HOME 'q\" #S"


def _recorder(tmp_path: Path, socket: str) -> Path:
    """A stand-in for `python -m empirica.core.cockpit.bar layout <session>`: records the window count it saw, and any overlap."""
    script = tmp_path / "relayout.sh"
    log = tmp_path / "relayout.log"
    running = tmp_path / "running.d"
    script.write_text(
        "#!/bin/sh\n"
        f"mkdir {shlex.quote(str(running))} 2>/dev/null || echo OVERLAP >> {shlex.quote(str(log))}\n"
        "sleep 0.4\n"
        f'n=$(tmux -L {socket} display-message -p -t "$2" "#{{session_windows}}")\n'
        f'echo "ran $1 $2 windows=$n" >> {shlex.quote(str(log))}\n'
        f"rmdir {shlex.quote(str(running))} 2>/dev/null\n"
    )
    script.chmod(0o755)
    return log


def _wait_quiet(log: Path, running: Path, seconds: float = 15.0) -> list[str]:
    """The log once nothing has been running for a full second."""
    deadline = time.monotonic() + seconds
    quiet_since = None
    while time.monotonic() < deadline:
        if running.exists():
            quiet_since = None
        elif quiet_since is None:
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since > 1.0:
            break
        time.sleep(0.05)
    return log.read_text().splitlines() if log.exists() else []


@pytest.fixture
def private_tmux(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.delenv("TMUX", raising=False)
    socket = f"empirica-test-{uuid.uuid4().hex[:12]}"

    def tmux(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["tmux", "-L", socket, *args], capture_output=True, text=True, timeout=10)

    started = tmux("-f", "/dev/null", "new-session", "-d", "-s", "cock", "-x", "160", "-y", "50", "sleep 120")
    assert started.returncode == 0, started.stderr
    socket_path = tmux("display-message", "-p", "#{socket_path}").stdout.strip()
    try:
        yield socket, tmux
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True, timeout=10)
        # tmux 3.6's kill-server leaves the socket file: 223 dead ones had piled up in /tmp/tmux-<uid> (goal 3dfcb8a3).
        if socket_path:
            Path(socket_path).unlink(missing_ok=True)


def _install(monkeypatch, tmp_path, socket, tmux) -> Path:
    state = tmp_path / AWKWARD
    monkeypatch.setattr(bar.launcher_state, "COCKPIT_DIR", state)
    monkeypatch.setattr(bar, "_tmux", lambda *a, **_k: tmux(*a))
    monkeypatch.setattr(bar, "cockpit_session_exists", lambda _s: True)
    monkeypatch.setattr(bar, "layout", lambda _s: 2)
    monkeypatch.setattr(bar, "install_bindings", lambda mouse=True: {})
    log = _recorder(tmp_path, socket)
    monkeypatch.setattr(bar, "_self_command", lambda: shlex.quote(str(tmp_path / "relayout.sh")))
    out = bar.install(SimpleNamespace(bar=True, mouse=True), "cock", config_path=None, profile=None)
    assert out["installed"]
    return log


def test_a_burst_of_hooks_coalesces_applies_the_last_event_and_opens_no_view_mode(monkeypatch, tmp_path, private_tmux):
    if shutil.which("flock") is None:
        pytest.skip("flock is util-linux; the fallback is tested below")
    socket, tmux = private_tmux
    log = _install(monkeypatch, tmp_path, socket, tmux)
    for _ in range(4):
        assert tmux("new-window", "-d", "-t", "cock", "sleep 120").returncode == 0
        time.sleep(0.05)
    lines = _wait_quiet(log, tmp_path / "running.d")
    modes = tmux("list-panes", "-s", "-t", "cock", "-F", "#{pane_id} #{pane_in_mode} #{pane_mode}").stdout.splitlines()
    assert modes and all(m.split()[1] == "0" for m in modes), modes  # no "returned 1" in view-mode
    assert "OVERLAP" not in lines, lines  # one relayout at a time
    ran = [line for line in lines if line.startswith("ran ")]
    assert ran and ran[-1] == "ran layout cock windows=5", lines  # the last window added was laid out
    assert len(ran) < 4, lines  # the burst coalesced
    assert (tmp_path / AWKWARD / "bar_state_cock.layout.lock").exists()  # the awkward path reached sh intact
    assert not list((tmp_path / AWKWARD).glob("*.dirty"))  # nothing left marked


def test_without_flock_the_hook_runs_the_relayout_once_and_succeeds(monkeypatch, tmp_path, private_tmux):
    """macOS has no flock: the hook runs the relayout unguarded and still exits 0."""
    socket, tmux = private_tmux
    _install(monkeypatch, tmp_path, socket, tmux)
    hook = tmux("show-hooks", "-t", "cock", "after-new-window").stdout
    assert "run-shell -b" in hook
    # The script tmux would run, rebuilt the way the hook builds it, under a PATH that has everything but flock.
    calls = []
    monkeypatch.setattr(bar, "_tmux", lambda *a, **_k: calls.append(a) or SimpleNamespace(stdout="", returncode=0))
    bar.install(SimpleNamespace(bar=True, mouse=True), "cock", config_path=None, profile=None)
    command = next(c[4] for c in calls if c[0] == "set-hook" and c[3] == "after-new-window")
    shell = bar._relayout_shell("cock")
    assert command == f"run-shell -b {bar._dq(shell.replace('#', '##'))}"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("touch", "rm", "mkdir", "rmdir", "sleep", "tmux"):
        found = shutil.which(tool)
        assert found
        os.symlink(found, bin_dir / tool)
    env = {"PATH": str(bin_dir), "HOME": str(tmp_path / "home")}
    out = subprocess.run(["/bin/sh", "-c", shell], capture_output=True, text=True, timeout=15, env=env)
    assert out.returncode == 0, out.stderr
    lines = (tmp_path / "relayout.log").read_text().splitlines()
    assert lines == ["ran layout cock windows=1"], lines

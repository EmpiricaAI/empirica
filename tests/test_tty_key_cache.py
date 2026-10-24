"""get_tty_key is computed once per parent pid and read from /proc, not from a ps per ancestor (ecodex prop_7sgyo7hcondstc7kgv5k4embi4).

The walk spawned one `ps` per ancestor level (up to five, ~22 ms each) and cached nothing, so one session-init called it 74 times and
one PreToolUse hook 4. Measured on the box that ran this: 74 calls took 3,077 ms. The result cannot change while the parent pid is the
same, so it is cached per process keyed on os.getppid() (a reparented listener or MCP server re-walks), and on Linux the answer is read
from /proc/<pid>/stat.

Properties kept from before: no PPID fallback (None when no tty can be determined), at most five levels, `/` replaced by `-`. New ones
this file pins: a definitive answer is cached, a transient failure (an unreadable process, a ps timeout) is NOT, and a device the /proc
decoder does not recognise falls back to ps instead of guessing a name.

/proc is a tmp_path tree and ps is a fake, so nothing here depends on the box that runs it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from empirica.utils import session_resolver as sr

PTS = lambda n: 136 * 256 + n  # noqa: E731 - the tty_nr of /dev/pts/<n> (major 136, minor n)


@pytest.fixture(autouse=True)
def clean_cache():
    sr.reset_tty_key_cache()
    yield
    sr.reset_tty_key_cache()


def _proc(root: Path, chain: dict[int, tuple[int, int]]):
    """Write /proc/<pid>/stat for each pid -> (ppid, tty_nr). The comm field is awkward on purpose: spaces and parentheses."""
    for pid, (ppid, tty_nr) in chain.items():
        d = root / str(pid)
        d.mkdir(parents=True, exist_ok=True)
        (d / "stat").write_text(
            f"{pid} (node (v22) x) S {ppid} {pid} {pid} {tty_nr} -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 1 0 0 0\n"
        )


@pytest.fixture
def proc(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_PROC_ROOT", tmp_path)

    def no_ps(*a, **k):
        raise AssertionError("ps must not be spawned when /proc answers")

    monkeypatch.setattr(sr.subprocess, "run", no_ps)
    return tmp_path


def _parent(monkeypatch, pid):
    monkeypatch.setattr(sr.os, "getppid", lambda: pid)


# ── the /proc path ───────────────────────────────────────────────────────────


def test_the_tty_is_read_from_proc_without_spawning_ps(proc, monkeypatch):
    _proc(proc, {200: (150, 0), 150: (100, PTS(2)), 100: (1, PTS(2))})
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() == "pts-2"


@pytest.mark.parametrize(
    ("tty_nr", "name"),
    [
        (PTS(0), "pts-0"),
        (PTS(7), "pts-7"),
        (137 * 256 + 3, "pts-259"),  # devpts index above 255 moves to the next major
        (4 * 256 + 1, "tty1"),
        (4 * 256 + 64, "ttyS0"),
        (4 * 256 + 66, "ttyS2"),
    ],
)
def test_the_device_number_decodes_to_the_name_ps_prints(proc, monkeypatch, tty_nr, name):
    _proc(proc, {200: (1, tty_nr)})
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() == name


def test_a_chain_with_no_tty_is_none_and_is_remembered(proc, monkeypatch):
    _proc(proc, {200: (150, 0), 150: (1, 0)})
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() is None
    for f in proc.glob("*/stat"):
        f.unlink()
    assert sr.get_tty_key() is None, "a chain that ends without a tty is a definitive answer"


def test_the_walk_stops_after_five_levels_as_before(proc, monkeypatch):
    _proc(
        proc,
        {200: (201, 0), 201: (202, 0), 202: (203, 0), 203: (204, 0), 204: (205, 0), 205: (206, 0), 206: (1, PTS(9))},
    )
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() is None, "the tty is on the seventh level; the old walk looked at five"


def test_a_tty_on_the_fifth_level_is_found(proc, monkeypatch):
    _proc(proc, {200: (201, 0), 201: (202, 0), 202: (203, 0), 203: (204, 0), 204: (1, PTS(4))})
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() == "pts-4"


# ── the cache ────────────────────────────────────────────────────────────────


def test_a_second_call_for_the_same_parent_reads_nothing(proc, monkeypatch):
    _proc(proc, {200: (1, PTS(2))})
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() == "pts-2"
    (proc / "200" / "stat").unlink()
    assert sr.get_tty_key() == "pts-2"


def test_a_reparented_process_walks_again(proc, monkeypatch):
    """A listener or MCP server outlives its first parent; the answer for the new parent must not be the old one."""
    _proc(proc, {200: (1, PTS(2)), 300: (1, PTS(5))})
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() == "pts-2"
    _parent(monkeypatch, 300)
    assert sr.get_tty_key() == "pts-5"
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() == "pts-2"


def test_an_unreadable_process_is_not_cached(proc, monkeypatch):
    """A pid that vanished mid-walk is a transient failure, not an answer: the next call must try again."""
    _parent(monkeypatch, 200)
    monkeypatch.setattr(
        sr.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="", stderr="gone")
    )
    assert sr.get_tty_key() is None
    _proc(proc, {200: (1, PTS(3))})
    assert sr.get_tty_key() == "pts-3"


# ── the ps path (macOS, or a device /proc cannot name) ───────────────────────


class _Ps:
    def __init__(self, table):
        self.table, self.calls = table, []

    def __call__(self, args, **kw):
        pid = int(args[2])
        self.calls.append(pid)
        if pid not in self.table:
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
        tty, ppid = self.table[pid]
        return subprocess.CompletedProcess(args, 0, stdout=f"{tty} {ppid}\n", stderr="")


def test_without_proc_the_ps_walk_is_unchanged_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_PROC_ROOT", tmp_path / "no-proc-here")
    ps = _Ps({200: ("??", 150), 150: ("pts/2", 100)})
    monkeypatch.setattr(sr.subprocess, "run", ps)
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() == "pts-2"
    assert ps.calls == [200, 150]
    assert sr.get_tty_key() == "pts-2" and ps.calls == [200, 150], "the second call must not spawn ps again"


def test_a_device_the_decoder_does_not_know_falls_back_to_ps_for_the_whole_walk(proc, monkeypatch):
    _proc(proc, {200: (1, 188 * 256 + 0)})  # /dev/ttyUSB0: not a name this decoder guesses
    ps = _Ps({200: ("ttyUSB0", 1)})
    monkeypatch.setattr(sr.subprocess, "run", ps)
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() == "ttyUSB0"
    assert ps.calls == [200]


def test_a_ps_timeout_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "_PROC_ROOT", tmp_path / "no-proc-here")
    state = {"fail": True}

    def ps(args, **kw):
        if state["fail"]:
            raise subprocess.TimeoutExpired(args, 2)
        return subprocess.CompletedProcess(args, 0, stdout="pts/8 1\n", stderr="")

    monkeypatch.setattr(sr.subprocess, "run", ps)
    _parent(monkeypatch, 200)
    assert sr.get_tty_key() is None
    state["fail"] = False
    assert sr.get_tty_key() == "pts-8"


def test_pid_one_or_lower_is_never_walked(proc, monkeypatch):
    _parent(monkeypatch, 1)
    assert sr.get_tty_key() is None

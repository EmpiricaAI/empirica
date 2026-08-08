"""INFRA_SAFE_PREFIXES must permit read-only recon and nothing else.

Two defects, found together while investigating the second.

**The bypass.** The prefix match returned True at :2239, ahead of the operator
and redirect guards at :2255, so under ``work_type=infra``::

    systemctl status nginx | sh        -> ALLOWED
    systemctl status nginx > /etc/hosts -> ALLOWED

and the caller reported both as ``"Safe Bash (read-only)"``. The ``&&`` case was
already caught by ``_classify_chain``, which is why this was easy to miss: two
of four hazards handled makes the branch look guarded. Both guards it needed
already existed and were already used elsewhere in the same function.

**The platform gap.** Every OS-specific entry was Linux. On a darwin fleet
``work_type=infra`` gated exactly the recon the tuple exists to permit — a bare
``launchctl list`` denied while ``systemctl list-units`` passed. The asymmetry
was precise: ``vmstat`` (Linux) present, ``vm_stat`` (darwin) absent, and all
eight darwin verbs had zero occurrences anywhere in the file. Reported by
empirica.philipp.empirica-mesh-support after hitting it live.

The gap mattered beyond convenience: to write honest claims at PREFLIGHT you
must first SEE the state, and reading that state is what got denied. The
reporter worked around it with ``pgrep``/``lsof``, which are ungated — so the
gate changed which tool was used, not whether the state was read.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("sentinel_gate_under_test", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def infra(gate, monkeypatch):
    monkeypatch.setattr(gate, "_current_work_type", "infra")
    return lambda cmd: gate.is_safe_bash_command({"command": cmd}) is True


# ─── The bypass ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "systemctl status nginx | sh",
        "systemctl status nginx | bash",
        "launchctl list | sh",
        "docker ps | sh",
    ],
)
def test_a_safe_prefix_piped_to_a_shell_is_gated(infra, command: str):
    """THE REGRESSION. A prefix match is a claim about the command WORD, not
    about the whole line."""
    assert not infra(command), f"executor smuggled past the allowlist: {command}"


@pytest.mark.parametrize(
    "command",
    [
        "systemctl status nginx > /etc/hosts",
        "systemctl status nginx >> /tmp/out",
        "lscpu > /tmp/cpu.txt",
    ],
)
def test_a_safe_prefix_with_a_file_redirect_is_gated(infra, command: str):
    """A redirect is a praxic side effect — the command WRITES."""
    assert not infra(command), f"redirect laundered past the allowlist: {command}"


def test_the_chain_case_stays_gated(infra):
    """Already caught by _classify_chain; pinned so the new guards don't
    accidentally reorder it into permissiveness."""
    assert not infra("systemctl status nginx && rm -rf /tmp/x")


def test_safe_stderr_redirects_are_still_permitted(infra):
    """`2>/dev/null` is noise suppression, not a side effect. Over-narrowing
    here would gate ordinary recon, which is the failure this whole file is
    about — in the other direction."""
    assert infra("systemctl status nginx 2>/dev/null")


# ─── The platform gap ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "launchctl list",
        "launchctl print gui/501",
        "sw_vers",
        "system_profiler SPHardwareDataType",
        "scutil --get ComputerName",
        "vm_stat",
        "diskutil list",
        "diskutil info /",
        "pmset -g",
        "plutil -p Info.plist",
        "plutil -lint Info.plist",
    ],
)
def test_darwin_read_only_recon_is_permitted(infra, command: str):
    assert infra(command), f"read-only darwin recon gated under work_type=infra: {command}"


@pytest.mark.parametrize(
    "command",
    [
        "launchctl bootout gui/501",
        "launchctl bootstrap gui/501 x.plist",
        "launchctl kickstart -k gui/501/x",
        "plutil -convert xml1 Info.plist",
        "plutil -replace Key -string v Info.plist",
        "diskutil eraseDisk JHFS+ x disk2",
        "diskutil unmount /Volumes/x",
    ],
)
def test_darwin_mutating_subcommands_stay_gated(infra, command: str):
    """`launchctl`, `plutil` and `diskutil` all have mutating subcommands, so
    the entries are scoped to read-only verbs rather than bare binaries."""
    assert not infra(command), f"mutating darwin subcommand permitted: {command}"


def test_linux_recon_is_unaffected(infra):
    """The platform fix must not regress the platform that already worked."""
    for command in ("systemctl status nginx", "journalctl -u empirica", "lscpu", "ip addr", "ss -tlnp"):
        assert infra(command), command


# ─── Scope ─────────────────────────────────────────────────────────────


def test_the_allowlist_does_not_apply_outside_its_work_types(gate, monkeypatch):
    """It is a work_type expansion, not a global widening."""
    monkeypatch.setattr(gate, "_current_work_type", "code")
    assert gate.is_safe_bash_command({"command": "launchctl list"}) is not True

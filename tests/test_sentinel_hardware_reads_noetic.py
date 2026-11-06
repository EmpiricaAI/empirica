"""Hardware and system reads flow ungated in every work type; the forms that stream without end do not.

Reported by empirica.david.empirica-mesh-support (prop_mxrzdihewrghvo64gdbgiaiyki): `lsusb` and `lspci` were refused between
transactions on a workstation. They were allowed only by INFRA_SAFE_PREFIXES, which applies only under work_type
infra/config/debug/remote-ops, and only to a single command with no pipe. None of these tools has a write mode, so they are reads
in any work type, inside a pipe, a chain or a `$(...)` body.

`vmstat`, `iostat` and `free` print once and exit, unless they are given an interval without a count, in which case they print
forever. Those forms stay where they were: allowed under the infra work types, gated elsewhere.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HOOK_DIR = Path(__file__).resolve().parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(scope="module")
def gate_module():
    sys.path.insert(0, str(_HOOK_DIR.parent / "lib"))
    spec = importlib.util.spec_from_file_location("sentinel_gate_hardware_reads", _HOOK_DIR / "sentinel-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(params=[None, "code", "research"])
def safe(gate_module, monkeypatch, request):
    """`safe(cmd)` is True when the gate lets `cmd` through ungated, in a work type that has no infra expansion."""
    monkeypatch.setattr(gate_module, "_current_work_type", request.param)
    return lambda cmd: gate_module.is_safe_bash_command({"command": cmd}) is True


READS = [
    # the two the practice was refused on
    "lsusb",
    "lspci",
    # the same families, with flags, pipes, chains and substitutions
    "lsusb -t",
    "lsusb -v -d 1d6b:0002",
    "lspci -nnk",
    "lspci -nnk | grep -A3 VGA",
    "lscpu",
    "lscpu | grep -E 'Model name|Thread'",
    "lsmem",
    "lsmem --summary",
    "free",
    "free -h",
    "free -h -s 1 -c 3",
    "free --seconds 1 --count 3",
    "uptime",
    "uptime -p",
    "lscpu; lsmem; free -h; uptime",
    'echo "$(uptime)"',
    "vmstat",
    "vmstat -s",
    "vmstat 1 5",
    "vmstat -w 1 5",
    "iostat",
    "iostat -x",
    "iostat -x 2 3",
    "iostat sda 2 3",
]


NOT_SAFE = [
    # an interval with no count never ends
    "vmstat 1",
    "vmstat -w 2",
    "iostat 2",
    "iostat -x 2",
    "iostat sda 2",
    "free -s 1",
    "free -h --seconds 2",
    # the streaming tools stay scoped to the infra work types
    "htop",
    "dmesg -w",
    "docker stats",
    # a read that writes, or chains into a write
    "lsusb > /tmp/usb.txt",
    "lscpu; rm -rf x",
    "uptime && touch x",
    "lspci | sh",
    # a longer word that only starts like one of them
    "uptimed --install",
    "freeze now",
    "lspcix",
    "lsusbx",
    "vmstatx",
]


@pytest.mark.parametrize("command", READS)
def test_a_hardware_or_system_read_is_ungated_in_every_work_type(safe, command):
    assert safe(command), command


@pytest.mark.parametrize("command", NOT_SAFE)
def test_a_stream_a_write_or_a_lookalike_is_still_gated(safe, command):
    assert not safe(command), command


@pytest.mark.parametrize("command", ["vmstat 1", "iostat 2", "free -s 1", "htop", "dmesg -w"])
def test_the_streaming_forms_are_still_allowed_under_infra_work(gate_module, monkeypatch, command):
    """Nothing that worked under the infra work types stops working."""
    monkeypatch.setattr(gate_module, "_current_work_type", "infra")
    assert gate_module.is_safe_bash_command({"command": command}) is True

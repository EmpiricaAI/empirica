"""A gate must never block the action that clears it — nor lie about why it did.

Reported by a peer: after a clean POSTFLIGHT, `preflight-submit` was refused with
*"Epistemic loop closed … Run new PREFLIGHT … Command: empirica preflight-submit -"*.
The deny prescribed exactly the command it had refused, so there was no operator
path out. A verbatim retry failed identically; so did a fresh session.

Probing the recogniser directly found an inconsistent heredoc parse:

    cd /x && empirica preflight-submit - << 'EOF'                 ALLOWED
    cd /x && empirica preflight-submit - << 'EOF' 2>&1 | tail -2  DENIED

The chain branch took the delimiter as the whole remainder of the line, so
`EOF' 2>&1 | tail -2` never matched a terminator. The single-statement branch
accepted the identical trailer. Same intent, opposite verdicts, by command shape.

**This is a security gate, so half of these tests assert what must STAY denied.**
The fix allows an inert trailer; it must not open the door it exists to hold shut —
a second command riding in behind a transition command.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "sentinel-gate.py"
)
P = '{"vectors":{"know":0.5}}'


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("sentinel_gate_shapes", _HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ALLOWED = {
    "bare heredoc": f"empirica preflight-submit - << 'EOF'\n{P}\nEOF",
    "heredoc, redirect+pipe on the line": f"empirica preflight-submit - << 'EOF' 2>&1 | tail -2\n{P}\nEOF",
    "cd && heredoc": f"cd /x && empirica preflight-submit - << 'EOF'\n{P}\nEOF",
    "cd && heredoc with inert trailer (THE REPORTED DEFECT)": (
        f"cd /x && empirica preflight-submit - << 'EOF' 2>&1 | tail -2\n{P}\nEOF"
    ),
    "cd NEWLINE heredoc with inert trailer": f"cd /x\nempirica preflight-submit - << 'EOF' 2>&1 | tail -2\n{P}\nEOF",
    "cd && heredoc, stderr silenced only": f"cd /x && empirica preflight-submit - << 'EOF' 2>/dev/null\n{P}\nEOF",
    "echo pipe": f"echo '{P}' | empirica preflight-submit -",
}

DENIED = {
    "a second command after the terminator": f"empirica preflight-submit - << 'EOF'\n{P}\nEOF\nempirica goals-list",
    "rm after the terminator": f"cd /x && empirica preflight-submit - << 'EOF'\n{P}\nEOF\nrm -rf /tmp/x",
    "chain trailer piping into python3 -c": (
        f"cd /x && empirica preflight-submit - << 'EOF' | python3 -c \"import os\"\n{P}\nEOF"
    ),
    "chain trailer redirecting to a real file": f"cd /x && empirica preflight-submit - << 'EOF' > /etc/hosts\n{P}\nEOF",
    "chain trailer with a chained command": f"cd /x && empirica preflight-submit - << 'EOF' ; rm -rf /x\n{P}\nEOF",
    "chain trailer with command substitution": f"cd /x && empirica preflight-submit - << 'EOF' | tail -$(rm x)\n{P}\nEOF",
    "prefix match then rm": "empirica preflight-submit payload.json\nrm -rf /tmp/x",
    "cd && rm": "cd /tmp && rm -rf /important",
    "rm piped into preflight": "rm -rf x | empirica preflight-submit -",
    "unterminated heredoc in a chain": f"cd /x && empirica preflight-submit - << 'EOF'\n{P}\n",
}


@pytest.mark.parametrize("name", sorted(ALLOWED))
def test_a_legitimate_preflight_shape_is_allowed(gate, name):
    assert gate.is_transition_command(ALLOWED[name]), f"refused a legitimate shape: {name}"


@pytest.mark.parametrize("name", sorted(DENIED))
def test_a_smuggled_second_command_stays_denied(gate, name):
    """The half that matters most. Loosening the parse must not open the door."""
    assert not gate.is_transition_command(DENIED[name]), f"ALLOWED a hostile shape: {name}"


@pytest.mark.parametrize(
    ("trailer", "inert"),
    [
        ("2>&1", True),
        ("2>&1 | tail -2", True),
        ("| head -5 | wc -l", True),
        ("2>/dev/null | grep ok", True),
        ("", True),
        ("| python3 -c 'x'", False),
        ("> out.txt", False),
        ("| tail -2 ; rm x", False),
        ("| tail -2 && rm x", False),
        ("| tail -$(id)", False),
        ("| tail -2 &", False),
        ("tail -2", False),
        ("|", False),
    ],
)
def test_the_inert_trailer_allowlist(gate, trailer, inert):
    assert gate._heredoc_trailer_is_inert(trailer) is inert

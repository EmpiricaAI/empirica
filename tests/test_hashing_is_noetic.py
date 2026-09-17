"""Hashing a file is a read, and the gate must not push you off it.

A bare `sha256sum <path>` was denied as praxic during mesh patch intake — one
command, no redirect, no chain — while `rg` and `Read` on the same file flowed.

That is worse than an inconvenience. **Hashing is the integrity check for bytes a
peer sent.** Gating it pushes the practitioner toward trusting the *sender* instead
of the bytes, so the gate discouraged the safer behaviour. A security control that
makes the careful path expensive gets routed around, and then it is protecting
nothing while still reading as protection.

Unlike `find`, `sort` or `sed`, none of these commands has a write mode, so they
need no flag inspection: there is no `--output` form to guard against.
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

_HASHERS = ["sha256sum", "sha1sum", "sha512sum", "md5sum", "b2sum", "cksum", "shasum"]


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("sentinel_gate", _HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_allowlist_is_reachable(gate):
    """Guards the suite's premise.

    If the constant were renamed, every assertion below would fail to find it and
    an `AttributeError` would read as a broken test rather than a broken gate.
    """
    assert isinstance(gate.SAFE_BASH_PREFIXES, tuple)
    assert "wc " in gate.SAFE_BASH_PREFIXES, "expected the existing read commands to still be here"


@pytest.mark.parametrize("cmd", _HASHERS)
def test_every_hasher_is_on_the_read_allowlist(gate, cmd):
    assert f"{cmd} " in gate.SAFE_BASH_PREFIXES, (
        f"`{cmd}` is not treated as a read. It has no write mode, and denying it "
        "pushes the practitioner toward trusting a peer's word over the peer's bytes."
    )


@pytest.mark.parametrize("cmd", _HASHERS)
def test_a_bare_hash_command_matches_by_prefix(gate, cmd):
    """The allowlist is prefix-matched, so assert on a realistic invocation.

    Membership in the tuple and "the command a practitioner actually types is
    matched" are different claims — the first is what I edited, the second is what
    was broken.
    """
    invocation = f"{cmd} /home/user/patch.diff"

    assert any(invocation.startswith(p) for p in gate.SAFE_BASH_PREFIXES), (
        f"{invocation!r} does not match any read prefix"
    )


def test_hashers_are_not_given_a_write_flag_exemption(gate):
    """They must not appear in the write-flag structures.

    The point is that they have no write mode. Listing one there would imply the
    opposite and invite someone to add a bypass for a flag that does not exist.
    """
    write_flags = getattr(gate, "_WRITE_FLAGS", {})

    for cmd in _HASHERS:
        assert cmd not in write_flags, f"{cmd} has no write mode; a write-flag entry would be misleading"


def test_a_redirect_still_gates(gate):
    """The positive control, and the one that keeps this from widening the gate.

    `sha256sum f > out` WRITES. If the prefix match alone decided the verdict,
    adding these commands would have opened a write path — so the redirect guard
    has to be what stops it, not the prefix list.
    """
    assert hasattr(gate, "SAFE_BASH_PREFIXES")
    # The command starts with an allowed prefix and must still not be treated as
    # safe overall, because of the redirect.
    cmd = "sha256sum /etc/hosts > /tmp/digest.txt"
    assert any(cmd.startswith(p) for p in gate.SAFE_BASH_PREFIXES), "prefix matches, as expected"

    assert gate._has_dangerous_redirects(cmd), (
        "a redirect must still read as a write regardless of the leading command — "
        "otherwise adding hashers to the prefix list would have opened a write path"
    )


def test_a_bare_hash_has_no_dangerous_redirect(gate):
    """Positive control for the control.

    Without this, `_has_dangerous_redirects` returning True for everything would
    satisfy the test above while gating the command this change exists to allow.
    """
    assert not gate._has_dangerous_redirects("sha256sum /etc/hosts")


def test_stderr_silencing_is_not_treated_as_a_write(gate):
    """`2>/dev/null` is the idiom every read in this codebase uses."""
    assert not gate._has_dangerous_redirects("sha256sum /etc/hosts 2>/dev/null")

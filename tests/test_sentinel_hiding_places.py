"""The Sentinel's read classifier cannot be talked into a write by text the shell never runs as arguments.

Broccoli sweep of 1.14.5, 2026-10-02 (a reviewer found them, I reproduced each with is_safe_bash_command):

- A trailing `# --help`, a heredoc body line or a quoted value `"--help"` counted as the help flag,
  because shlex.split keeps comments and heredoc text as tokens. Any `empirica-workspace` write passed
  (my own 69633f7fc) and so did any `empirica` verb.
- A read followed by a redirect or a process substitution passed: the statement branch ran before the
  redirect check (`empirica goals-list > file`, `empirica goals-list <(rm -rf x)`).
- `env` was a blanket safe prefix, so `env rm -rf x` passed; and bare-word prefixes had no word
  boundary (`setfacl -R` starts with `set`).

Each negative control is paired with the legitimate form that must keep working, because a gate that
refuses everything also passes the negatives.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"


@pytest.fixture(scope="module")
def gate():
    sys.modules.pop("sentinel_gate", None)
    spec = importlib.util.spec_from_file_location("sentinel_gate", HOOKS / "sentinel-gate.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(HOOKS.parent / "lib"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.pop(0)
    return mod


def safe(gate, command: str) -> bool:
    return gate.is_safe_bash_command({"command": command})


# `empirica rebuild` is a verb that is gated (not on any tier); `org create` is a workspace write.
GATED_EMPIRICA = "empirica rebuild --qdrant-only"
WORKSPACE_WRITE = "empirica-workspace org create --name X"


@pytest.mark.parametrize("base", [GATED_EMPIRICA, WORKSPACE_WRITE])
@pytest.mark.parametrize(
    "hiding_place",
    [
        " # --help",
        " # -h",
        " # --version",
        ' --reason "--help"',
        " --reason '--help'",
        " <<EOF\n--help\nEOF",
        "\n--help",
    ],
)
def test_a_help_flag_in_a_comment_quote_or_heredoc_does_not_bless_a_write(gate, base, hiding_place):
    assert safe(gate, base + hiding_place) is False


@pytest.mark.parametrize("command", [f"cd /tmp && {WORKSPACE_WRITE} # --help", f"echo hi | {WORKSPACE_WRITE} # --help"])
def test_the_same_inside_a_chain_or_pipe(gate, command):
    assert safe(gate, command) is False


@pytest.mark.parametrize(
    "command",
    [
        "empirica rebuild --help",
        "empirica-workspace org create --help",
        "empirica-workspace --help",
        "empirica --version",
        "empirica goals-archive --help",
    ],
)
def test_a_real_help_argument_is_still_inert(gate, command):
    """CONTROL: the shortcut still works for what it was written for."""
    assert safe(gate, command) is True


@pytest.mark.parametrize(
    "command",
    [
        "empirica goals-list > /tmp/out.txt",
        "empirica goals-list >> ~/.bashrc",
        "empirica goals-list <(rm -rf /tmp/zz)",
        "empirica goals-list >(sh)",
        "empirica-workspace org list >> ~/.bashrc",
        "empirica-workspace contact list --output json > /tmp/contacts.json",
    ],
)
def test_a_read_followed_by_a_redirect_or_process_substitution_is_not_a_read(gate, command):
    assert safe(gate, command) is False


@pytest.mark.parametrize(
    "command",
    [
        "empirica goals-list",
        "empirica doctor 2>/dev/null",
        "empirica goals-list >/dev/null",
        "empirica-workspace org list --output json",
        'empirica log-artifacts - <<\'EOF\'\n{"nodes": [{"data": {"finding": "a > b, and x -> y"}}]}\nEOF',
    ],
)
def test_legitimate_reads_and_payloads_with_angle_brackets_keep_flowing(gate, command):
    """CONTROL: a `>` inside a heredoc payload or a stderr suppression is not a file redirect."""
    assert safe(gate, command) is True


@pytest.mark.parametrize(
    "command",
    [
        "env rm -rf /tmp/zz",
        "env X=1 rm -rf /tmp/zz",
        "env X=1 Y=2 rm -rf /tmp/zz",
        f"env X=1 {WORKSPACE_WRITE}",
        f"env EMPIRICA_INSTANCE_ID=x {GATED_EMPIRICA}",
        "env -i rm -rf /tmp/zz",
    ],
)
def test_env_runs_what_follows_so_it_is_judged_by_what_follows(gate, command):
    assert safe(gate, command) is False


@pytest.mark.parametrize(
    "command",
    [
        "env",
        "env | grep EMPIRICA",
        "env EMPIRICA_INSTANCE_ID=empirica-nle empirica mailbox poll --output json",
        'env EMPIRICA_INSTANCE_ID="empirica-nle" empirica goals-list',
        "env X=1",
    ],
)
def test_env_in_front_of_a_safe_command_and_bare_env_still_flow(gate, command):
    """CONTROL: empirica-nle prefixes every call with `env EMPIRICA_INSTANCE_ID=...`; that must work."""
    assert safe(gate, command) is True


@pytest.mark.parametrize(
    "command", ["setfacl -R -m u:x:rwx /tmp", "idle-thing --now", "calibre-convert x", "dateutil-fix"]
)
def test_a_bare_word_prefix_ends_at_a_word_boundary(gate, command):
    assert safe(gate, command) is False


@pytest.mark.parametrize("command", ["id", "date", "set -e", "pwd", "whoami", "uname -a", "hostname"])
def test_the_bare_words_themselves_still_flow(gate, command):
    """CONTROL: the boundary rule must not break the commands the prefixes were written for."""
    assert safe(gate, command) is True

"""Two defects in the same branch, pointing opposite ways.

Between transactions — POSTFLIGHT submitted, no new PREFLIGHT — the Sentinel is
supposed to keep blocking file modification while letting reads and the artifact
lifecycle through. Both halves were wrong, and only one of them is the kind of
wrong that gets reported.

**The over-gate.** `sqlite3 -header -column <db> "SELECT …"` was DENIED. `-column`
sat in the value-taking flag set, but it is a bare output-mode flag exactly like
`-header` and `-box` — it is `-mode column` that takes a value. Listed as
value-taking it swallowed the token after it, which is the DB PATH, leaving one
positional; the shape check reads one positional as an interactive REPL and
denies. So `-header <db> "SELECT"` passed and `-header -column <db> "SELECT"` did
not, and the refusal said *Epistemic loop closed*, naming nothing about flags.

A denied read is not a harmless conservatism. It teaches the practitioner to
rubber-stamp a CHECK to get at information, which corrupts the gate's meaning far
more than it protects anything.

**The bypass, found while fixing the over-gate.** `is_safe_empirica_command`
matches on the LEADING verb and is consulted on whole commands at three sites —
including as the last resort in this very branch. So:

    empirica goals-list
    rm -rf /some/path

was ALLOWED. Line 1 is a Tier-1 read; line 2 was never looked at. Any destructive
command rides behind a safe empirica verb.

The two are not symmetric and the tests should not read as though they are. An
over-gate is visible the moment it fires. A false allow is silent by construction
— nobody reports a command that ran.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_GATE = (
    pathlib.Path(__file__).resolve().parent.parent / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"
)
_spec = importlib.util.spec_from_file_location("sentinel_gate_bt", _GATE)
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)


def loop_closed_verdict(command: str) -> bool:
    """Replay the loop-closed branch's predicate chain, in its real order.

    Mirrors the `if postflight_ts > preflight_ts` block rather than driving the
    whole hook, because reaching that block for real needs a DB with a POSTFLIGHT
    newer than its PREFLIGHT. The ORDER is the load-bearing part — the bypass
    exists precisely because a chain-blind predicate sits last and rescues what
    the chain-aware one correctly rejected.
    """
    if gate.is_safe_bash_command({"command": command}):
        return True
    if gate.is_toggle_command(command):
        return True
    if gate.is_transition_command(command):
        return True
    return bool(gate.is_safe_empirica_statement(command))


# ── the over-gate ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "flags",
    ["", "-header", "-column", "-header -column", "-box", "-json", "-line", "-header -json -column"],
    ids=lambda f: (f or "no-flags").replace(" ", "+"),
)
def test_every_display_flag_combination_leaves_a_select_readable(flags):
    """Parametrised so a failure names WHICH flag broke it. `-column` alone was
    the one that did, and it was invisible next to `-header` passing."""
    cmd = f'sqlite3 {flags} /tmp/x.db "SELECT a FROM t"'.replace("  ", " ")
    assert gate.is_safe_sqlite_command(cmd), f"a pure read was gated by flags: {flags!r}"


def test_the_flag_that_really_takes_a_value_still_consumes_it():
    """NEGATIVE CONTROL on the fix. `-mode column` DOES take a value, and dropping
    it from the set would push the db path into the query slot — the same defect
    arrived at from the other direction."""
    assert gate.is_safe_sqlite_command('sqlite3 -mode column /tmp/x.db "SELECT 1"')


@pytest.mark.parametrize("verb", ["DROP TABLE t", "DELETE FROM t", "INSERT INTO t VALUES (1)", "UPDATE t SET a=1"])
def test_a_write_stays_denied_whatever_the_flags(verb):
    """The fix widens what counts as a READ. It must not widen what counts as
    safe — a `-column` write is still a write."""
    assert not gate.is_safe_sqlite_command(f'sqlite3 -header -column /tmp/x.db "{verb}"')


def test_a_bare_sqlite3_is_still_a_repl():
    """No query means an interactive session, which can write. The shape check
    that produced the over-gate is CORRECT — it was being fed a mangled token
    list, not asking the wrong question."""
    assert not gate.is_safe_sqlite_command("sqlite3 /tmp/x.db")


def test_the_over_gate_is_gone_at_the_branch_that_denied_it():
    """End of the chain, not just the helper: this is the exact command that was
    refused with *Epistemic loop closed*."""
    assert loop_closed_verdict('sqlite3 -header -column /tmp/x.db "SELECT a FROM t"')


# ── the bypass ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "joiner",
    ["\n", " ; ", " && ", " || ", " | ", " & "],
    ids=["newline", "semicolon", "and", "or", "pipe", "background"],
)
def test_nothing_destructive_rides_behind_a_safe_empirica_verb(joiner):
    """THE bypass. A Tier-1 read on the left, anything at all on the right."""
    assert not loop_closed_verdict(f"empirica goals-list{joiner}rm -rf /tmp/definitely-not-real")


def test_the_two_predicates_answer_different_questions_on_purpose():
    """The fix is a NAME, and this is what the name buys.

    Folding the guard into the verb predicate was the first attempt and it broke
    five heredoc tests: the pipe splitter strips `<<` from the segment carrying
    it, so the receiving half of `cat <<'JSON' | empirica log-artifacts -` arrives
    as `empirica log-artifacts -\\n{}\\nJSON` — a legitimate single statement
    wearing a multi-statement shape, which no guard inside the predicate can tell
    from the bypass.

    So *command* answers "is this VERB safe?" and *statement* answers "is this
    whole input safe?". A caller now makes a visible choice instead of an
    invisible omission.
    """
    orphan_body = "empirica log-artifacts -\n{}\nJSON"
    assert gate.is_safe_empirica_command(orphan_body), "the verb predicate must still see a safe verb"
    assert gate.is_safe_empirica_command("empirica goals-list\nrm -rf /tmp/x"), (
        "and it must remain verb-only — a guard here is what broke the heredoc path"
    )

    assert gate.is_safe_empirica_statement("empirica goals-list")
    assert not gate.is_safe_empirica_statement("empirica goals-list\nrm -rf /tmp/x")


@pytest.mark.parametrize(
    "cmd",
    [
        "empirica check-submit - <<'EOF'\n{\"vectors\": {}}\nEOF",
        "empirica log-artifacts - <<'JSON'\n{\"nodes\": []}\nJSON",
        "empirica postflight-submit - <<-EOF\n{}\nEOF",
    ],
    ids=["check", "log-artifacts", "dash-heredoc"],
)
def test_a_heredoc_is_one_statement_however_many_lines_it_spans(cmd):
    """Every JSON payload in this system arrives this way. A guard that counted
    body newlines as separators would not tighten anything — it would disable
    PREFLIGHT."""
    assert gate.is_safe_empirica_statement(cmd)


def test_a_command_after_the_heredoc_terminator_is_a_second_statement():
    """Where a bypass would actually hide once the body is skipped. Only a line
    holding the delimiter ALONE closes it, so a stray `EOF` inside the JSON
    cannot end the scan early."""
    assert not gate.is_safe_empirica_statement("empirica log-artifacts - <<'EOF'\n{}\nEOF\nrm -rf /tmp/x")
    assert gate.is_safe_empirica_statement('empirica log-artifacts - <<\'EOF\'\n{"note": "EOF marker"}\nEOF')


def test_an_executor_cannot_be_reached_through_a_pipe():
    """`| sh` is the sharpest form: the left side is genuinely safe and the right
    side runs whatever the left printed."""
    assert not loop_closed_verdict("empirica goals-list | sh")
    assert not loop_closed_verdict("empirica project-search --task x | bash")


# ── what must still work, or the fix is a new over-gate ──────────────────────


@pytest.mark.parametrize(
    "cmd",
    [
        "empirica goals-list",
        "empirica goals-create --objective 'X'",
        "empirica goals-complete --goal-id abc --reason 'done'",
        "empirica finding-log --finding 'x'",
        "empirica unknown-resolve abc --resolution 'y'",
        "empirica goals-list | head -5",
        "empirica project-search --task 'x' --output json | rg goal_id",
        "empirica mailbox poll --output json",
    ],
)
def test_the_artifact_lifecycle_still_flows_between_transactions(cmd):
    """The whole point of the branch: closing goals, resolving unknowns and
    logging artifacts must work with no transaction open, or the discipline the
    gate exists to encourage becomes impossible to practise."""
    assert loop_closed_verdict(cmd), f"artifact lifecycle blocked: {cmd}"


def test_a_separator_inside_a_quoted_argument_is_not_a_separator():
    """Quote-awareness, and it is load-bearing: findings and rationales routinely
    contain `|` and `&&`. A naive substring check would have made this fix a
    worse over-gate than the one it repairs."""
    assert gate.is_safe_empirica_command("""empirica finding-log --finding "a | b && c" """.strip())
    assert loop_closed_verdict("""empirica finding-log --finding "a; b" """.strip())


def test_a_write_tool_is_still_denied_between_transactions():
    """POSITIVE CONTROL on the branch's actual purpose. If this passes, the
    branch has stopped gating anything and every test above is vacuous."""
    assert not loop_closed_verdict("rm -rf /tmp/definitely-not-real")
    assert not loop_closed_verdict("echo x > /tmp/definitely-not-real")

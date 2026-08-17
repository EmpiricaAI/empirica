"""Reading is noetic — the Sentinel must not gate empirica's read-only verbs.

The gate carried a hand-maintained allowlist against a CLI that exposes 279
verbs. Measured 2026-07-27: **41 read-only verbs were on neither tier**, so they
were denied before CHECK — the whole `engagement-*` family (a peer practice hit
exactly that), `session-show`, `sources-map`, `projects-list`, and more.

Why it matters beyond ergonomics: denying a READ teaches the practitioner to
rubber-stamp a CHECK in order to look something up. That corrupts the gate's
meaning far more than the gate protects anything, because CHECK is supposed to
mean "I have grounded myself", not "I needed to run a query".

These pin the CONTRACT — read-shaped verbs flow, mutating verbs do not — rather
than the specific verb list, which is what failed the first time.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

GATE = (
    Path(__file__).resolve().parents[1]
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "sentinel-gate.py"
)


@pytest.fixture(scope="module")
def gate():
    spec = importlib.util.spec_from_file_location("sentinel_gate", GATE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# The measured casualties. Each was denied pre-CHECK before this fix.
PREVIOUSLY_GATED_READS = [
    "empirica engagement-list",
    "empirica engagement-show eng-1",
    "empirica engagement-walk eng-1",
    "empirica session-show abc",
    "empirica sessions-list",
    "empirica source-list",
    "empirica source-get src-1",
    "empirica sources-map",
    "empirica projects-list",
    "empirica identity-list",
    "empirica identity-verify sess-1",
    "empirica checkpoint-list",
    "empirica checkpoint-diff a b",
    "empirica checkpoint-verify s",
    "empirica checkpoint-signatures s",
    "empirica concept-stats",
    "empirica concept-top",
    "empirica concept-related x",
    "empirica daemon-list",
    "empirica daemon-grants-list",
    "empirica issue-show 12",
    "empirica issue-stats",
    "empirica memory-report",
    "empirica qdrant-status",
    "empirica sync-status",
    "empirica system-status",
    "empirica trajectory-show s",
    "empirica trajectory-stats",
    "empirica bus-status",
    "empirica scan-diff",
    "empirica scan-history",
    "empirica scan-show",
    "empirica workspace-search foo",
    "empirica docs-explain thing",
    "empirica bootstrap-context",
    # Verified read-only by reading the handler, not the help text:
    # query_commands.py contains no INSERT/UPDATE/commit/write.
    "empirica query findings",
]


# The three verbs that LOOK read-only and are not. Each was left gated on
# evidence, and `scan` is the cautionary one: its help literally says
# "(read-only)" — meaning it does not mutate the SERVICES it inspects — while
# `scan_commands.py` opens three files for write, which is how `scan-history`
# has anything to show. Help text is not a contract.
VERBS_THAT_LOOK_READONLY_BUT_WRITE = [
    "empirica scan",  # writes scan record + last + history
    "empirica vision",  # has a `vision log` subcommand
    "empirica module",  # validate today; fetch/provision slated
]


@pytest.mark.parametrize("cmd", VERBS_THAT_LOOK_READONLY_BUT_WRITE)
def test_verbs_that_write_stay_gated(gate, cmd):
    assert gate.is_read_shaped_empirica_verb(cmd) is False
    assert gate.is_safe_empirica_command(cmd) is False, (
        f"{cmd!r} mutates state and must not flow pre-CHECK — see the handler, not the help text"
    )


@pytest.mark.parametrize("cmd", PREVIOUSLY_GATED_READS)
def test_read_only_verbs_are_not_gated(gate, cmd):
    assert gate.is_safe_empirica_command(cmd) is True, f"{cmd!r} is a pure read and must flow pre-CHECK"


# Mutating verbs that must NOT be swept in. The gate is still a gate.
MUTATING_VERBS = [
    "empirica sources-check",  # writes review stamps — the near-miss `-check` case
    "empirica entity-create --name x",
    "empirica entity-delete e1",
    "empirica entity-create --name x",
    "empirica engagement-update eng-1",
    "empirica entity-reindex",
    "empirica qdrant-cleanup",
    "empirica project-init",
    "empirica sync-push",
    "empirica release",
    "empirica serve",
]


@pytest.mark.parametrize("cmd", MUTATING_VERBS)
def test_mutating_verbs_are_not_swept_in_by_the_suffix_rule(gate, cmd):
    """The suffix rule must not launder a mutating verb. Tier 2 may still allow
    some of these as workflow verbs — what is asserted here is narrower: the
    READ-SHAPE rule itself must not be what lets them through."""
    assert gate.is_read_shaped_empirica_verb(cmd) is False, f"{cmd!r} must not be classified read-shaped"


def test_suffix_rule_matches_the_verb_not_the_flags(gate):
    """A mutating verb cannot be laundered by appending a read-shaped word, and a
    read verb is not disqualified by its flags."""
    assert gate.is_read_shaped_empirica_verb("empirica entity-create --name my-list") is False
    assert gate.is_read_shaped_empirica_verb("empirica engagement-list --status open") is True


def test_non_empirica_commands_are_untouched(gate):
    assert gate.is_read_shaped_empirica_verb("rm -rf /tmp/list") is False
    assert gate.is_read_shaped_empirica_verb("git show HEAD") is False
    assert gate.is_read_shaped_empirica_verb("empirica") is False


def test_workflow_verbs_still_allowed(gate):
    """Regression guard: the tiers must keep working alongside the new rule."""
    for cmd in ("empirica finding-log --finding x", "empirica preflight-submit -", "empirica log-artifacts -"):
        assert gate.is_safe_empirica_command(cmd) is True


def test_help_and_version_bypass_survives(gate):
    for cmd in ("empirica entity-create --help", "empirica --version"):
        assert gate.is_safe_empirica_command(cmd) is True

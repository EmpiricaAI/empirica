"""CHECK must CERTIFY, not UNLOCK — instrument and message.

A peer measured **339 of 728 CHECKs (47%) submitted within 30 seconds of their
PREFLIGHT**, median gap 33.6s. Routine, not a rare lapse. 458 transactions went
PREFLIGHT → praxic directly, so the correct path is reachable and used; both
behaviours coexist in one practice.

Two of the causes are testable here:

**The gap was unmeasurable.** CHECK and POSTFLIGHT wrote epistemic snapshots;
PREFLIGHT never did. So `cascade_phase` held only two phases and a PREFLIGHT→CHECK
join returned zero pairs — the one number that says whether an intervention worked
was the one number nobody had. Instrument before intervening.

**The guard's message misreported its own predicate.** The deny fires on a
CONJUNCTION — `duration < 30s AND findings == 0 AND unknowns == 0` — but read
*"Rushed assessment (11s). Investigate and log learnings first."* Naming only the
elapsed time teaches "wait longer", which is the one remedy that does not work:
waiting 30s with nothing logged still denies, and logging one finding at 5s already
passes. Same family as the artifact-breadth nudge whose predicate could not be
satisfied — a signal whose stated remedy is not the one it wants.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GATE = REPO / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "sentinel-gate.py"


# ── the instrument ────────────────────────────────────────────────────


def test_preflight_writes_an_epistemic_snapshot(tmp_path, monkeypatch):
    """THE regression. Without a PREFLIGHT row there is no opening timestamp, so
    the PREFLIGHT→CHECK gap cannot be computed at all."""
    from empirica.cli.command_handlers import _workflow_preflight as P

    captured = {}

    class _Provider:
        def create_snapshot_from_session(self, session_id, context_summary, cascade_phase, domain_vectors=None):
            captured["phase"] = cascade_phase
            captured["semantic"] = context_summary.semantic

            class _S:
                snapshot_id = "snap-1"
                vectors = None

            return _S()

        def save_snapshot(self, snapshot):
            captured["saved"] = True

    monkeypatch.setattr("empirica.data.snapshot_provider.EpistemicSnapshotProvider", _Provider)

    out = P._preflight_create_snapshot("s-1", {"know": 0.6, "uncertainty": 0.4}, "why", "cp-1", "tx-1")

    assert out == "snap-1"
    assert captured["phase"] == "PREFLIGHT", "the phase label is what makes the gap joinable"
    assert captured["semantic"]["phase"] == "PREFLIGHT"
    assert captured["saved"] is True


def test_snapshot_failure_never_breaks_preflight(monkeypatch):
    """A measurement surface must not be able to block the transaction it measures."""
    from empirica.cli.command_handlers import _workflow_preflight as P

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("provider down")

    monkeypatch.setattr("empirica.data.snapshot_provider.EpistemicSnapshotProvider", _Boom)

    assert P._preflight_create_snapshot("s-1", {"know": 0.5}, "r", "cp", "tx") is None


def test_missing_uncertainty_does_not_raise(monkeypatch):
    from empirica.cli.command_handlers import _workflow_preflight as P

    seen = {}

    class _Provider:
        def create_snapshot_from_session(self, session_id, context_summary, cascade_phase, domain_vectors=None):
            seen["conf"] = context_summary.semantic["confidence"]

            class _S:
                snapshot_id = "x"
                vectors = None

            return _S()

        def save_snapshot(self, snapshot):
            pass

    monkeypatch.setattr("empirica.data.snapshot_provider.EpistemicSnapshotProvider", _Provider)

    P._preflight_create_snapshot("s-1", {}, None, None, None)
    assert seen["conf"] == pytest.approx(0.5), "absent uncertainty falls back to neutral, not a crash"


# ── the guard message ─────────────────────────────────────────────────


def _deny_message() -> str:
    """The rush-guard deny string, read from source.

    Read rather than executed: the guard is a hook that needs a live session, a
    populated DB and a real transaction to reach this branch. Pinning the text is
    what matters — the DEFECT WAS THE TEXT, not the logic.
    """
    src = GATE.read_text(encoding="utf-8")
    match = re.search(r'return \(\s*"deny",\s*(f?"CHECK submitted.*?)\n\s*\)', src, re.DOTALL)
    assert match, "rush-guard deny message not found — did the branch move?"
    return match.group(1)


def test_the_deny_names_the_artifact_condition_not_only_the_clock():
    """THE regression. The old message said only 'Rushed assessment (11s)', so the
    lesson taken was 'wait longer' — the one remedy that does not work."""
    msg = _deny_message()

    assert "findings or unknowns" in msg, "the deny must name the condition that actually triggers it"
    assert "finding-log" in msg, "and the concrete verb that satisfies it"


def test_the_deny_says_that_one_artifact_is_enough():
    """Quantity matters: a practitioner who thinks they need to 'do a lot of
    investigating' will pad. One artifact passes, and saying so removes the excuse
    for ceremony in the other direction."""
    msg = _deny_message()

    assert "ONE artifact" in msg


def test_the_deny_says_more_time_alone_does_not_work():
    """The specific false lesson has to be contradicted explicitly, or the reader
    keeps the model the old message gave them."""
    msg = _deny_message()

    assert "more time alone does not" in msg.lower()


def test_the_deny_offers_skipping_as_a_legitimate_path():
    """Mechanism 2 of the ceremony problem: skipping CHECK reads as omission and
    therefore as laziness, while an empty CHECK feels like doing the process. The
    deny is the highest-attention moment available to say otherwise."""
    msg = _deny_message()

    assert "skip CHECK" in msg
    assert "not a shortcut" in msg


def test_the_predicate_is_still_a_conjunction():
    """Guard against 'fixing' the message by loosening the condition. A peer
    described this predicate as wall-clock; it is not, and the artifact half is
    what makes the deny satisfiable rather than a waiting game."""
    src = GATE.read_text(encoding="utf-8")

    assert "if findings == 0 and unknowns == 0:" in src
    assert "noetic_duration < min_duration" in src


# ── T3: grounded at open ──────────────────────────────────────────────


def _load_gate():
    """Load the hook as a module. It is standalone by design (no package import),
    so it is loaded by path rather than imported."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("sentinel_gate_under_test", GATE)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


class _Cursor:
    """Minimal cursor over an in-memory transaction_claims table."""

    def __init__(self, rows):
        import sqlite3

        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE transaction_claims (id TEXT, session_id TEXT, transaction_id TEXT, "
            "claim_index INT, claim TEXT, grounding TEXT, declared_timestamp REAL)"
        )
        for i, (sid, tx, grounding) in enumerate(rows):
            self.conn.execute(
                "INSERT INTO transaction_claims VALUES (?,?,?,?,?,?,?)",
                (f"c{i}", sid, tx, i, "a claim", grounding, 0.0),
            )
        self.conn.commit()
        self._cur = self.conn.cursor()

    def execute(self, *a, **k):
        return self._cur.execute(*a, **k)

    def fetchone(self):
        return self._cur.fetchone()


@pytest.mark.parametrize("grounding", ["read", "ran"])
def test_a_claim_grounded_by_read_or_ran_certifies_the_transaction(grounding):
    """THE T3 regression. Grounding happens BEFORE the window opens routinely —
    noetic work is ungated — and until now that could not be stated, so the
    practitioner either filed an empty CHECK or got denied."""
    gate = _load_gate()
    cur = _Cursor([("s-1", "tx-1", grounding)])

    assert gate._has_grounded_claims(cur, "s-1", "tx-1") is True


@pytest.mark.parametrize("grounding", ["assumed", "retrieved"])
def test_weak_grounding_does_NOT_certify(grounding):
    """The asymmetry that stops this becoming a new rubber stamp. `assumed` is by
    definition the absence of grounding, and `retrieved` is our own prior artifact
    — testimony, not observation. Neither can substitute for having looked."""
    gate = _load_gate()
    cur = _Cursor([("s-1", "tx-1", grounding)])

    assert gate._has_grounded_claims(cur, "s-1", "tx-1") is False


def test_claims_from_another_transaction_do_not_certify_this_one():
    gate = _load_gate()
    cur = _Cursor([("s-1", "tx-other", "read")])

    assert gate._has_grounded_claims(cur, "s-1", "tx-1") is False


def test_no_claims_at_all_does_not_certify():
    gate = _load_gate()

    assert gate._has_grounded_claims(_Cursor([]), "s-1", "tx-1") is False


def test_it_fails_CLOSED_when_the_table_is_missing():
    """A gate that fails open is worse than one that occasionally asks for a CHECK
    you did not need. A pre-062 database has no transaction_claims table."""
    import sqlite3

    gate = _load_gate()

    class _Bare:
        def __init__(self):
            self.conn = sqlite3.connect(":memory:")
            self._cur = self.conn.cursor()

        def execute(self, *a, **k):
            return self._cur.execute(*a, **k)

        def fetchone(self):
            return self._cur.fetchone()

    assert gate._has_grounded_claims(_Bare(), "s-1", "tx-1") is False


def test_the_no_check_deny_names_the_grounded_at_open_path():
    """Mechanism 2, addressed where it is actually read. The old deny said only
    'Run CHECK after investigation', which is why skipping read as omission — at
    the one moment the practitioner is definitely reading, we told them ceremony
    was the only way through."""
    src = GATE.read_text(encoding="utf-8")
    match = re.search(r'"deny",\s*\n\s*("No CHECK, and no grounded claims.*?)\n\s*\)', src, re.DOTALL)
    assert match, "no-CHECK deny message not found"
    msg = match.group(1)

    assert "claims" in msg, "it must name the alternative path"
    assert "read or ran" in msg, "and what actually certifies"
    assert "CORRECT path, not a shortcut" in msg, "and that skipping when grounded is correct"

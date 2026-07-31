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

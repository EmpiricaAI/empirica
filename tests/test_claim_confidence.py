"""CHECK's `confidence` was `1 - uncertainty` relabelled (1,933 of 1,933 rows), so it added nothing to the uncertainty vector (goal d3657e40).

`claim_confidence` is computed from something the uncertainty vector does not contain: the grounding of the claims the practitioner declared
at CHECK, weighted by how often claims grounded that way actually held. Measured on this project, among claims with a verdict of held or
refuted (1,768 claims in all): read 97.4%, ran 95.5%, retrieved 82.4%, assumed 70.0%. Those verdicts are the practitioner's own, so the figure
is self-graded and says so.
"""

from __future__ import annotations

import inspect

import pytest

from empirica.core import claims as C
from empirica.data.session_database import SessionDatabase

PRIOR = {"read": 0.974, "ran": 0.955, "retrieved": 0.824, "assumed": 0.700}


def rates(**override):
    base = {g: {"rate": r, "decided": 0, "source": "prior"} for g, r in PRIOR.items()}
    for g, r in override.items():
        base[g] = {"rate": r, "decided": 99, "source": "practice"}
    return base


def stored(*groundings):
    return [{"grounding": g, "claim": f"c{i}"} for i, g in enumerate(groundings)]


# ── the computation ──


def test_no_claims_means_no_figure_not_a_neutral_one():
    assert C.claim_confidence([], rates()) is None


def test_one_claim_is_the_hold_rate_of_its_grounding():
    assert C.claim_confidence(stored("read"), rates())["value"] == pytest.approx(0.974)
    assert C.claim_confidence(stored("assumed"), rates())["value"] == pytest.approx(0.700)


def test_several_claims_multiply_because_the_work_rests_on_all_of_them():
    out = C.claim_confidence(stored("read", "ran", "assumed"), rates())
    assert out["value"] == pytest.approx(0.974 * 0.955 * 0.700, abs=1e-3)
    assert out["claims"] == 3 and out["by_grounding"] == {"read": 1, "ran": 1, "assumed": 1}


def test_an_unlabelled_claim_counts_as_assumed_and_is_reported():
    """An unlabelled claim is not evidence of grounding; scoring it as the weakest kind is the safe reading, and it is reported."""
    out = C.claim_confidence([{"grounding": None, "claim": "x"}, {"grounding": "read", "claim": "y"}], rates())
    assert out["value"] == pytest.approx(0.700 * 0.974, abs=1e-3)
    assert out["unlabelled"] == 1


def test_it_says_where_it_comes_from_and_that_the_verdicts_are_self_graded():
    out = C.claim_confidence(stored("read", "assumed"), rates(read=0.9))
    assert out["self_graded"] is True and "independent" in out["assumes"]
    assert out["rates_from"] == {"read": "practice", "assumed": "prior"}
    assert "untested" in out["assumes"], "the caveat that unadjudicated claims are excluded travels with the figure"
    assert out["hold_rates"] == {"read": 0.9, "assumed": 0.700}


def test_a_stronger_grounding_mix_scores_higher_than_a_weaker_one():
    strong = C.claim_confidence(stored("read", "ran"), rates())["value"]
    weak = C.claim_confidence(stored("retrieved", "assumed"), rates())["value"]
    assert strong > weak + 0.3


# ── not 1 - uncertainty, by construction ──


def test_the_figure_has_no_uncertainty_input_so_it_cannot_be_derived_from_it():
    """The defect was a value computed from `1.0 - uncertainty`. A function that is not given the uncertainty cannot reproduce it."""
    assert "uncertainty" not in inspect.signature(C.claim_confidence).parameters


def test_the_same_claims_give_the_same_figure_whatever_the_uncertainty_was():
    claims = stored("read", "assumed")
    first = C.claim_confidence(claims, rates())["value"]
    for uncertainty in (0.05, 0.3, 0.6, 0.9):
        assert C.claim_confidence(claims, rates())["value"] == first != pytest.approx(1 - uncertainty)


# ── where the hold rates come from ──


@pytest.fixture
def db(tmp_path):
    d = SessionDatabase(db_path=str(tmp_path / "s.db"))
    yield d
    d.close()


def add_claims(db, grounding, held, refuted, untested=0):
    rows = [("held",)] * held + [("refuted",)] * refuted + [(None,)] * untested
    for i, (verdict,) in enumerate(rows):
        db.conn.execute(
            "INSERT INTO transaction_claims (id, session_id, transaction_id, claim_index, claim, grounding, verdict, declared_timestamp) "
            "VALUES (?, 's', 't', ?, 'c', ?, ?, 0.0)",
            (f"{grounding}-{i}", i, grounding, verdict),
        )
    db.conn.commit()


def test_a_few_decided_claims_only_nudge_the_prior_and_are_labelled_blended(db):
    """Four decided claims must not replace a prior built from 1,768: the rate is pulled toward the prior by MIN_DECIDED pseudo-observations."""
    add_claims(db, "read", held=3, refuted=1)
    r = C.hold_rates(db)["read"]
    assert r["source"] == "blended" and r["decided"] == 4
    assert r["rate"] == pytest.approx((3 + C.MIN_DECIDED * PRIOR["read"]) / (4 + C.MIN_DECIDED), abs=1e-3)


def test_with_no_decided_claims_the_prior_is_used_unchanged(db):
    r = C.hold_rates(db)
    assert all(r[g] == {"rate": PRIOR[g], "decided": 0, "source": "prior"} for g in PRIOR)


def test_this_practices_own_rate_dominates_once_it_has_many_decided_claims(db):
    add_claims(db, "assumed", held=150, refuted=150)  # 300 decided, 50%
    r = C.hold_rates(db)["assumed"]
    assert r["source"] == "practice" and r["decided"] == 300
    assert r["rate"] == pytest.approx((150 + 20 * 0.7) / 320, abs=1e-3)
    assert 0.5 < r["rate"] < 0.7, "pulled toward the prior, not equal to either"


def test_a_rate_is_never_exactly_zero_or_one_so_a_figure_never_claims_certainty(db):
    """20 refuted gave exactly 0.0 and 20 held gave exactly 1.0 (certainty) in the first version."""
    add_claims(db, "assumed", held=0, refuted=25)
    add_claims(db, "read", held=25, refuted=0)
    r = C.hold_rates(db)
    assert 0.0 < r["assumed"]["rate"] < 0.7 and 0.974 < r["read"]["rate"] < 1.0


def test_untested_claims_are_not_counted_as_held_or_refuted(db):
    """'Never checked' says nothing about whether the claim held; counting it either way would move the rate on no evidence."""
    add_claims(db, "ran", held=20, refuted=0, untested=200)
    assert C.hold_rates(db)["ran"]["decided"] == 20


def test_groundings_are_counted_under_their_normalised_name(db):
    """Rows 'Read' and 'read' were separate groups and the second silently overwrote the first."""
    add_claims(db, "read", held=10, refuted=0)
    for i in range(10):
        db.conn.execute(
            "INSERT INTO transaction_claims (id, session_id, transaction_id, claim_index, claim, grounding, verdict, declared_timestamp) "
            "VALUES (?, 's', 't', ?, 'c', 'Read', 'refuted', 0.0)",
            (f"R-{i}", 100 + i),
        )
    db.conn.commit()
    assert C.hold_rates(db)["read"]["decided"] == 20


def test_the_priors_are_the_measured_ones():
    assert C.PRIOR_HOLD_RATES == PRIOR


def test_a_missing_grounding_in_the_rates_falls_back_to_its_prior_instead_of_raising():
    out = C.claim_confidence(stored("read"), {"assumed": {"rate": 0.5, "decided": 0, "source": "prior"}})
    assert out["value"] == pytest.approx(0.974) and out["rates_from"] == {"read": "prior"}


# ── what check-submit writes and reports ──

import empirica.cli.command_handlers._workflow_check as wc  # noqa: E402


class _RecordingBus:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


def test_check_submit_stops_writing_one_minus_uncertainty_as_confidence(monkeypatch):
    """The checkpoint metadata, the CHECK_COMPLETE bus event and the snapshot's semantic dict each carried `confidence = 1 - uncertainty`,
    which read like a measurement and was the uncertainty vector again."""
    seen: dict = {}

    class _Logger:
        def __init__(self, *a, **k):
            pass

        def add_checkpoint(self, **kw):
            seen["metadata"] = kw["metadata"]
            return "cp-1"

    bus = _RecordingBus()
    import empirica.core.bus_persistence as bp
    import empirica.core.canonical.git_enhanced_reflex_logger as gl
    import empirica.core.epistemic_bus as eb

    monkeypatch.setattr(gl, "GitEnhancedReflexLogger", _Logger)
    monkeypatch.setattr(bp, "wire_persistent_observers", lambda **k: None)
    monkeypatch.setattr(eb, "get_global_bus", lambda: bus)
    checkpoint_id, _tx, gaps = wc._check_store_and_publish(
        "s", 1, {"know": 0.8, "uncertainty": 0.2}, "proceed", "why", 1
    )
    assert checkpoint_id == "cp-1" and isinstance(gaps, list)
    assert "confidence" not in seen["metadata"]
    assert seen["metadata"]["decision"] == "proceed", "the rest of the metadata is unchanged"
    assert bus.events and "confidence" not in bus.events[0].data
    assert bus.events[0].data["decision"] == "proceed"


def test_the_snapshot_semantic_summary_no_longer_carries_the_relabelled_scalar(monkeypatch):
    captured: dict = {}

    class _Snap:
        snapshot_id = "snap-1"

        def __init__(self):
            self.vectors = {}

    class _Provider:
        def create_snapshot_from_session(self, **kw):
            captured["semantic"] = kw["context_summary"].semantic
            return _Snap()

        def save_snapshot(self, snap):
            pass

    import empirica.data.snapshot_provider as sp

    monkeypatch.setattr(sp, "EpistemicSnapshotProvider", _Provider)
    monkeypatch.setattr(wc, "_get_db_for_session", lambda _sid: type("D", (), {"close": lambda self: None})())
    assert wc._check_create_snapshot("s", {"uncertainty": 0.2}, "proceed", "why", 1, "cp-1") == "snap-1"
    assert "confidence" not in captured["semantic"] and captured["semantic"]["decision"] == "proceed"


def test_the_claims_echo_carries_claim_confidence(db, monkeypatch):
    monkeypatch.setattr(wc, "_get_db_for_session", lambda _sid: db)
    monkeypatch.setattr(db, "close", lambda: None)
    sid = db.create_session(ai_id="cc")
    block = wc._check_declare_claims(
        sid,
        "tx-1",
        [
            {"claim": "a", "grounding": "read", "ref": "x.py:1"},
            {"claim": "b", "grounding": "assumed"},
        ],
    )
    cc = block["claim_confidence"]
    assert cc["claims"] == 2 and cc["by_grounding"] == {"read": 1, "assumed": 1}
    assert cc["value"] == pytest.approx(0.974 * 0.700, abs=1e-3), (
        "a fresh DB has no decided claims, so the priors apply"
    )
    assert cc["rates_from"] == {"read": "prior", "assumed": "prior"}


def test_without_claims_there_is_no_claim_confidence_block():
    assert wc._check_declare_claims("s", "tx", None) is None


def test_the_result_reports_the_labelled_claim_confidence_block_or_null_never_a_bare_number():
    """A bare top-level float would drop `self_graded`, `assumes` and `rates_from`, and read as calibrated."""
    block = {"value": 0.68, "claims": 2, "self_graded": True, "assumes": "x", "rates_from": {"read": "prior"}}
    assert wc._claim_confidence_block({"declared": 2, "claim_confidence": block}) == block
    assert wc._claim_confidence_block(None) is None
    assert wc._claim_confidence_block({"declared": 0, "claims": []}) is None


# ── the legacy `check` command ──


def test_the_legacy_check_echoes_a_confidence_only_when_the_caller_supplied_one():
    """It used to answer `1 - uncertainty` when none was given. A caller-asserted confidence is information; a derived one is not."""
    assert wc._legacy_check_confidence_fields(0.78, None) == {"confidence": 0.78, "claim_confidence": None}
    assert wc._legacy_check_confidence_fields(None, None) == {"claim_confidence": None}


def test_the_legacy_check_reports_claim_confidence_from_the_claims_block():
    fields = wc._legacy_check_confidence_fields(None, {"declared": 1, "claim_confidence": {"value": 0.974}})
    assert fields == {"claim_confidence": {"value": 0.974}}


def test_the_legacy_check_still_decides_on_the_asserted_or_derived_readiness():
    """The decision needs a number; only what is REPORTED as confidence changed."""
    assert wc._check_readiness(0.9, 0.5) == 0.9
    assert wc._check_readiness(None, 0.3) == pytest.approx(0.7)


def test_the_documented_confidence_flag_is_actually_read(monkeypatch):
    """`empirica check --confidence 0.75` is documented and registered on the parser, but the handler read only the payload key, so the
    flag was accepted and discarded without an error. A flat 0.0 is a real value, so it must not be treated as absent."""
    import types

    monkeypatch.setattr(wc, "_parse_workflow_input", lambda _a, _n: ({"confidence": 0.4}, "json"))
    base = {"session_id": "s", "cycle": None, "round": None, "verbose": False}
    assert wc._check_cmd_parse_inputs(types.SimpleNamespace(confidence=0.75, **base))["explicit_confidence"] == 0.75
    assert wc._check_cmd_parse_inputs(types.SimpleNamespace(confidence=0.0, **base))["explicit_confidence"] == 0.0
    assert wc._check_cmd_parse_inputs(types.SimpleNamespace(confidence=None, **base))["explicit_confidence"] == 0.4


@pytest.mark.parametrize("bad", [7.0, -0.1, float("nan"), float("inf")])
def test_an_asserted_confidence_outside_zero_to_one_is_refused_not_passed_through(monkeypatch, bad):
    """Now that the flag is read, `--confidence 7` would make readiness 7 and the check proceed whatever the uncertainty, and nan would
    reach the result as invalid JSON."""
    import types

    monkeypatch.setattr(wc, "_parse_workflow_input", lambda _a, _n: ({}, "json"))
    args = types.SimpleNamespace(confidence=bad, session_id="s", cycle=None, round=None, verbose=False)
    with pytest.raises(ValueError, match="between 0 and 1"):
        wc._check_cmd_parse_inputs(args)


def test_a_payload_confidence_is_validated_the_same_way(monkeypatch):
    import types

    monkeypatch.setattr(wc, "_parse_workflow_input", lambda _a, _n: ({"confidence": 78}, "json"))
    args = types.SimpleNamespace(confidence=None, session_id="s", cycle=None, round=None, verbose=False)
    with pytest.raises(ValueError, match="between 0 and 1"):
        wc._check_cmd_parse_inputs(args)

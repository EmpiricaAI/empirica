"""A key that is accepted and then dropped must be SAID, when it looks like a typo.

`PreflightInput` forbids unknown keys — switched after a payload keyed
`task_description` was accepted and lost. Its siblings `CheckInput` and
`PostflightInput` were left at pydantic's default `extra='ignore'`. So a POSTFLIGHT
sending `"claim"` for `"claims"` drops every adjudication, forces them all to
`untested`, and reports ok. **The fix went to the model that bit, not the class.**

Found while sweeping for siblings of the 2026-09-17 incident, where `scope` and
`count` were accepted on every call and stored on none — that one produced by
version skew rather than a typo, but through the same permissive door: `claims` is
`list[dict]`, so any key validates.

Not fixed by flipping the siblings to `forbid`: that hard-fails CHECK and POSTFLIGHT
fleet-wide for any caller carrying a stray key, a hot-path outage to cure a
visibility problem. Report, do not reject — and only near-misses, because the first
draft flagged every undeclared key and would have fired on every CHECK.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from empirica.cli.validation import RAW_CONSUMED, CheckInput, PostflightInput, ignored_keys, safe_validate

BASE = {"session_id": "s", "vectors": {"know": 0.5, "uncertainty": 0.5}}


@pytest.mark.parametrize(
    ("model", "extra", "expected"),
    [
        (PostflightInput, {"claim": [{"index": 1, "verdict": "held"}]}, "claims"),
        (CheckInput, {"reasonning": "r"}, "reasoning"),
        (PostflightInput, {"vector": {}}, "vectors"),
    ],
)
def test_a_top_level_near_miss_is_named_with_its_intended_key(model, extra, expected):
    out = ignored_keys({**BASE, **extra}, model)

    assert len(out) == 1
    assert f"did you mean {expected!r}" in out[0], "an unnamed typo cannot be acted on"


def test_the_adjudication_typo_that_silently_untests_every_claim():
    """The sharpest instance: nothing fails, every verdict is lost."""
    out = ignored_keys({**BASE, "claim": [{"index": 1, "verdict": "held"}]}, PostflightInput)

    assert out == ["claim (did you mean 'claims'?)"]


def test_a_near_miss_inside_a_claim_is_located_by_index():
    out = ignored_keys({**BASE, "claims": [{"claim": "a"}, {"claim": "b", "scop": "pop", "coun": 3}]}, CheckInput)

    assert "claims[2].scop (did you mean 'scope'?)" in out
    assert "claims[2].coun (did you mean 'count'?)" in out
    assert not any(o.startswith("claims[1]") for o in out)


def test_the_documented_check_payload_is_silent():
    """Positive control, and the one that matters most.

    The system prompt's own CHECK example carries `current_phase`, which no handler
    reads. The first draft of this detector flagged it — and `claims` too, because
    handlers read that off the RAW payload rather than the model — so it would have
    fired on every CHECK anyone submitted. Two mechanisms in this codebase already
    died of over-firing.
    """
    payload = {
        **BASE,
        "current_phase": "noetic",
        "reasoning": "r",
        "claims": [{"claim": "c", "grounding": "ran", "ref": "f.py:1", "scope": "pop", "count": 3}],
    }

    assert ignored_keys(payload, CheckInput) == []


def test_benign_unrelated_extras_are_not_typos():
    """`work_type` on a POSTFLIGHT is ignored and harmless. Not a near-miss of
    anything, so not reported — no allowlist of 'harmless' keys to go stale."""
    assert ignored_keys({**BASE, "work_type": "code", "notes": "n"}, PostflightInput) == []


def test_every_key_the_claims_module_reads_is_accepted():
    from empirica.cli.validation import _CLAIM_KEYS

    claim = dict.fromkeys(_CLAIM_KEYS, "x")
    assert ignored_keys({**BASE, "claims": [claim]}, CheckInput) == []


def test_the_note_reaches_stderr_and_never_the_return_value(capsys):
    validated, err = safe_validate({**BASE, "claim": []}, PostflightInput)

    assert err is None and validated is not None, "reporting must not turn into rejecting"
    stderr = capsys.readouterr().err
    assert "ACCEPTED and IGNORED" in stderr
    assert "still report ok" in stderr, "say WHY it matters: nothing else will look wrong"


def test_a_clean_submission_prints_nothing(capsys):
    safe_validate({**BASE, "reasoning": "r"}, CheckInput)

    assert capsys.readouterr().err == ""


# ─── guard the guard ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("model_name", "handler"),
    [("CheckInput", "_workflow_check.py"), ("PostflightInput", "_workflow_postflight.py")],
)
def test_raw_consumed_matches_what_the_handler_actually_reads(model_name, handler):
    """`RAW_CONSUMED` is a hand-written list over another file's behaviour — the
    exact shape that went stale twice in `VALID_POLL_STATUSES`. So it is checked
    against the source it describes: every `config_data.get("...")` in the handler.

    If a handler starts reading a new raw key and this is not updated, the detector
    would report that key's near-misses against a set that omits it — covering less
    than it claims, silently.
    """
    src = (Path(__file__).resolve().parent.parent / "empirica" / "cli" / "command_handlers" / handler).read_text(
        encoding="utf-8"
    )
    actually_read = set(re.findall(r'config_data\.get\("([a-z_]+)"', src))

    assert actually_read, "the scan found nothing — it is looking for the wrong thing"
    assert actually_read == set(RAW_CONSUMED[model_name]), (
        f"{handler} reads {sorted(actually_read - set(RAW_CONSUMED[model_name]))} that RAW_CONSUMED omits, "
        f"and RAW_CONSUMED lists {sorted(set(RAW_CONSUMED[model_name]) - actually_read)} it no longer reads"
    )

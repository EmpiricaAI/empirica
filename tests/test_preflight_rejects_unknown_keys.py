"""PREFLIGHT must reject unknown top-level keys rather than silently dropping them.

The cost of dropping them is not proportional to the typo. `task_context` is the
sole driver of Qdrant pattern retrieval in
`_workflow_preflight._preflight_retrieve_patterns`:

    search_context = task_context or reasoning
    if not (search_context and project_id):
        return None

So a payload keyed `task_description` (or any near-miss) left `task_context` at its
"" default, returned ok:true, and the caller worked the whole transaction with no
lessons, dead-ends, prior mistakes or findings — surfaced only as `patterns: null`,
which is indistinguishable from "retrieval ran and found nothing relevant".

POSTFLIGHT already rejects unknown keys for the same reason (GH #402/#409). These
tests pin the symmetry so it cannot regress.
"""

import pytest
from pydantic import ValidationError

from empirica.cli.validation import PreflightInput


VALID_MINIMUM = {
    "session_id": "11111111-2222-3333-4444-555555555555",
    "vectors": {"know": 0.5, "uncertainty": 0.5},
}


def test_rejects_near_miss_key_that_would_cost_all_retrieval():
    """`task_description` is the real-world miss: plausible, and silently fatal."""
    with pytest.raises(ValidationError) as exc:
        PreflightInput(**VALID_MINIMUM, task_description="opening a transaction")

    assert "task_description" in str(exc.value)


def test_rejects_typo_of_a_real_key():
    with pytest.raises(ValidationError) as exc:
        PreflightInput(**VALID_MINIMUM, task_contxt="typo")

    assert "task_contxt" in str(exc.value)


def test_error_names_every_offending_key():
    """Naming the offenders is what turns a mystery into a one-word fix."""
    with pytest.raises(ValidationError) as exc:
        PreflightInput(**VALID_MINIMUM, task_description="x", zzz_invented=123)

    message = str(exc.value)
    assert "task_description" in message
    assert "zzz_invented" in message


def test_accepts_the_documented_optional_fields():
    """The guard must not narrow what a well-formed payload may carry."""
    model = PreflightInput(
        **VALID_MINIMUM,
        reasoning="why this baseline",
        task_context="what the work is, used for pattern retrieval",
        work_context="investigation",
        work_type="infra",
        domain="default",
        criticality="medium",
        claims=[{"claim": "grounded at open", "grounding": "read"}],
    )

    assert model.task_context.startswith("what the work is")
    assert model.work_type == "infra"
    assert model.claims and model.claims[0]["grounding"] == "read"


def test_minimum_valid_payload_still_constructs():
    model = PreflightInput(**VALID_MINIMUM)

    assert model.vectors["know"] == 0.5
    # Unset optionals keep their documented defaults rather than becoming required.
    assert model.task_context == ""
    assert model.reasoning == ""

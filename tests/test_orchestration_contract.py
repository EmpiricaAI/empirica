"""The proposal contract moved from Cortex into core, and what changed at the seam.

David ruled on 2026-11-06 that the four contract files move into MIT core under empirica/core/orchestration/ (schemas,
target_acks, audit, leak_guard), contract files only, so a local server and Cortex speak one contract. Cortex's own
tests/test_leak_guard.py was run against core's copy before the move landed (21 passed). These tests hold the parts that are
core's now:

- core names no identity store: `from_request` takes a `canonicalize` function where Cortex passed its tenant database;
- `derive_mesh_layer` came along, and is pure;
- one table of credential shapes with two tiers: exact for the proposal gate, loose for redaction, and `core/redaction.py`
  scrubs exactly what it scrubbed before it read the table;
- importing the package loads nothing, because `core/redaction.py` imports the leak guard on the artifact write path.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from empirica.core import redaction
from empirica.core.orchestration import audit, leak_guard, schemas, target_acks
from empirica.core.orchestration.schemas import Proposal, derive_mesh_layer

KEY = "ctx_empirica_adm_" + "0123456789abcdef" * 2
FIXTURE_KEY = "ctx_test_adm_" + "0123456789abcdef" * 2
NTFY = "tk_" + "a1b2c3d4e5f6g7h8i9j0k1l2"
JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkYXZpZCJ9.c2lnbmF0dXJlc2lnbmF0dXJl"


# ── the seam ────────────────────────────────────────────────────────────────────────────────────────────────────────────────


def _request(**over):
    body = {"title": "t", "summary": "s", "source_claude": "cortex", "target_claudes": ["core", "web"], **over}
    return body


def test_without_a_lookup_ids_pass_through_unchanged():
    prop = Proposal.from_request(_request(), org_id="org-empirica")
    assert prop.source_claude == "cortex" and prop.target_claudes == ["core", "web"]


def test_the_lookup_the_caller_passes_canonicalizes_the_source_and_every_target():
    seen: list[str] = []

    def canonical(ai_id: str) -> str:
        seen.append(ai_id)
        return f"empirica.david.{ai_id}"

    prop = Proposal.from_request(_request(), org_id="org-empirica", canonicalize=canonical)

    assert prop.source_claude == "empirica.david.cortex"
    assert prop.target_claudes == ["empirica.david.core", "empirica.david.web"]
    assert seen == ["cortex", "core", "web"]
    assert prop.layer == "L2", "the layer is derived from the canonical ids"


def test_core_names_no_identity_store():
    import inspect

    params = inspect.signature(Proposal.from_request).parameters
    assert "canonicalize" in params and "tenant_db" not in params and "source_user_id" not in params
    import ast

    for module in (schemas, target_acks, audit, leak_guard):
        tree = ast.parse(inspect.getsource(module))
        imported = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        imported += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [m for m in imported if m == "cortex" or m.startswith("cortex.")], module.__name__


@pytest.mark.parametrize(
    ("source", "targets", "layer"),
    [
        ("empirica.david.core", ["empirica.david.core"], "L1"),
        ("cortex", ["cortex"], "L1"),
        ("empirica.david.core", ["empirica.david.web"], "L2"),
        ("empirica.david.core", ["empirica.philipp.core"], "L2"),
        ("empirica.david.core", ["other.x.y"], "L3"),
        ("empirica.david.core", ["empirica.david.web", "other.x.y"], "L3"),
        ("empirica.david.core", ["core"], "L3"),
        ("", ["empirica.david.core"], "L3"),
        ("empirica.david.core", [], "L3"),
    ],
)
def test_the_mesh_layer_is_the_strictest_across_targets(source, targets, layer):
    assert derive_mesh_layer(source, targets) == layer


def test_a_layer_the_caller_gives_wins():
    assert Proposal.from_request(_request(layer="L1"), org_id="o").layer == "L1"


def test_an_envelope_survives_its_own_round_trip():
    prop = Proposal.from_request(_request(payload={"k": [1, 2]}), org_id="o")
    again = Proposal.from_payload(prop.to_payload())
    assert (again.title, again.target_claudes, again.payload, again.layer) == (
        prop.title,
        prop.target_claudes,
        prop.payload,
        prop.layer,
    )


# ── the gate and the table ──────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("field", "value"), [("title", KEY), ("summary", f"use {NTFY}"), ("payload", {"t": JWT})])
def test_validate_refuses_a_credential_and_says_where(field, value):
    prop = Proposal.from_request(_request(**{field: value}), org_id="o")
    error = prop.validate()
    assert error and field in error and "credential" in error


def test_the_fixture_key_form_is_allowed_by_the_gate():
    assert Proposal.from_request(_request(summary=f"fixture {FIXTURE_KEY}"), org_id="o").validate() is None


def test_the_gate_uses_the_exact_tier_and_redaction_the_loose_one():
    loose_only = "ctx_" + "abcdefgh12"  # a short key: redaction takes it, the gate does not
    assert leak_guard.find_secrets(loose_only) == []
    assert redaction.redact_secrets(loose_only) != loose_only
    for name, (exact, loose) in leak_guard.SHAPES.items():
        assert leak_guard.LOOSE[name] is loose and exact is not loose


def test_redaction_reads_the_shared_table_and_scrubs_what_it_scrubbed_before():
    """The three patterns redaction.py held as literals until the move, character for character."""
    before = {
        "cortex_api_key": r"\bctx_[A-Za-z0-9_\-]{8,}",
        "ntfy_token": r"\btk_[A-Za-z0-9]{20,}",
        "jwt": r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]+",
    }
    assert {name: rx.pattern for name, rx in leak_guard.LOOSE.items()} == before
    in_redaction = [rx for rx, _ in redaction._SHAPE_PATTERNS]
    for rx in leak_guard.LOOSE.values():
        assert any(r is rx for r in in_redaction), rx.pattern
    text = f"key {KEY} token {NTFY} jwt {JWT}"
    clean = redaction.redact_secrets(text)
    assert KEY not in clean and NTFY not in clean and JWT not in clean


def test_the_marker_names_the_kind_and_nothing_of_the_value():
    assert leak_guard.redact(f"a {KEY} b") == "a [REDACTED:cortex_api_key] b"
    assert leak_guard.redact(None) is None
    assert leak_guard.first_secret("clean", f"x {NTFY}") == (1, "ntfy_token")
    assert leak_guard.scan_all([KEY, None, "nothing"]) == ["cortex_api_key"]


# ── the other two modules ───────────────────────────────────────────────────────────────────────────────────────────────────


def test_an_audit_entry_appends_and_a_mixed_set_of_acks_aggregates():
    log = audit.append_audit([], audit.created_entry("core", note="n"))
    assert len(log) == 1 and log[0]["actor"] == "core" and log[0]["action"] == "created"
    assert target_acks.aggregate_result({"a": {"result": "shipped"}, "b": {"result": "shipped"}}) == "shipped"
    assert target_acks.aggregate_result({"a": {"result": "shipped"}, "b": {"result": "failed"}}) != "shipped"


def test_importing_the_package_loads_none_of_its_modules():
    """core/redaction.py imports the leak guard when an artifact is written; the envelope must not come with it."""
    code = (
        "import sys, empirica.core.orchestration as o; "
        "print(sorted(m for m in sys.modules if m.startswith('empirica.core.orchestration.')))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout.strip()
    assert out == "[]"


def test_from_request_signature_is_what_cortex_calls():
    """Cortex imports the contract from core and calls from_request(body, org_id=..., source_fallback=..., canonicalize=...)
    with canonicalize keyword-only and last (prop_zrlzl26acffnrnxgjbnpwskzo4). Changing this breaks Cortex's import of a
    released core; change it only together with Cortex."""
    import inspect

    from empirica.core.orchestration.schemas import Proposal

    params = list(inspect.signature(Proposal.from_request).parameters.values())
    assert [p.name for p in params] == ["body", "org_id", "source_fallback", "canonicalize"]
    assert [p.kind.name for p in params[1:]] == ["KEYWORD_ONLY"] * 3
    assert params[-1].default is None and params[2].default == ""

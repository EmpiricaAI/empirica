"""Tests for the strict canonical 3-form mesh seat composition + persistence.

The `seat` parameter of cortex_session_init and the strict-canonical send
resolver expect the DOT-form `org.tenant.project` (decoded via
`split('.', 2)`). A wrong-form seat is rejected silently, so empirica must
compose it correctly and persist it into project.yaml for the daemon/model
to pass.
"""

from __future__ import annotations

import yaml

from empirica.cli.command_handlers.setup_claude_code import _persist_tenant_metadata
from empirica.config.project_config_loader import compose_canonical_seat

# ── composer ─────────────────────────────────────────────────────────


def test_compose_canonical_seat_basic():
    assert compose_canonical_seat(mesh_id_prefix="empirica.david", ai_id="empirica") == "empirica.david.empirica"


def test_compose_canonical_seat_strips_trailing_dot():
    # cortex hands the prefix without a trailing dot, but be defensive.
    assert (
        compose_canonical_seat(mesh_id_prefix="empirica.david.", ai_id="empirica-cortex")
        == "empirica.david.empirica-cortex"
    )


def test_compose_canonical_seat_none_on_empty():
    # Don't manufacture a malformed seat — wrong-form seats fail silently.
    assert compose_canonical_seat(mesh_id_prefix="", ai_id="empirica") is None
    assert compose_canonical_seat(mesh_id_prefix="empirica.david", ai_id="") is None
    assert compose_canonical_seat(mesh_id_prefix="  ", ai_id="  ") is None


# ── persistence (self-healing canonical_seat) ────────────────────────


def _write_yaml(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(obj, sort_keys=False), encoding="utf-8")


def test_persist_writes_canonical_seat(tmp_path):
    """Persisting tenant fields also derives + writes the canonical_seat."""
    pj = tmp_path / ".empirica" / "project.yaml"
    _write_yaml(pj, {"ai_id": "empirica"})

    changed = _persist_tenant_metadata(
        tmp_path,
        org_id="org-empirica",
        tenant_slug="david",
        mesh_id_prefix="empirica.david",
    )
    assert changed is True
    out = yaml.safe_load(pj.read_text())
    assert out["mesh_id_prefix"] == "empirica.david"
    assert out["canonical_seat"] == "empirica.david.empirica"


def test_persist_heals_seat_when_core_fields_unchanged(tmp_path):
    """A project.yaml that already has mesh_id_prefix + ai_id but no seat
    gets the seat backfilled even though the three core fields don't change."""
    pj = tmp_path / ".empirica" / "project.yaml"
    _write_yaml(
        pj,
        {
            "ai_id": "empirica",
            "org_id": "org-empirica",
            "tenant_slug": "david",
            "mesh_id_prefix": "empirica.david",
            # no canonical_seat
        },
    )

    changed = _persist_tenant_metadata(
        tmp_path,
        org_id="org-empirica",
        tenant_slug="david",
        mesh_id_prefix="empirica.david",
    )
    assert changed is True  # seat heal alone is a change
    out = yaml.safe_load(pj.read_text())
    assert out["canonical_seat"] == "empirica.david.empirica"


def test_persist_idempotent_when_seat_already_present(tmp_path):
    pj = tmp_path / ".empirica" / "project.yaml"
    _write_yaml(
        pj,
        {
            "ai_id": "empirica",
            "org_id": "org-empirica",
            "tenant_slug": "david",
            "mesh_id_prefix": "empirica.david",
            "canonical_seat": "empirica.david.empirica",
        },
    )

    changed = _persist_tenant_metadata(
        tmp_path,
        org_id="org-empirica",
        tenant_slug="david",
        mesh_id_prefix="empirica.david",
    )
    assert changed is False


def test_persist_no_seat_without_mesh_prefix(tmp_path):
    """No mesh_id_prefix → no seat written (can't compose a valid one)."""
    pj = tmp_path / ".empirica" / "project.yaml"
    _write_yaml(pj, {"ai_id": "empirica"})

    changed = _persist_tenant_metadata(
        tmp_path,
        org_id="org-empirica",
        tenant_slug="david",
        mesh_id_prefix=None,
    )
    out = yaml.safe_load(pj.read_text())
    assert "canonical_seat" not in out
    # org_id + tenant_slug still persisted
    assert changed is True
    assert out["org_id"] == "org-empirica"


def test_compose_canonical_seat_passthrough_already_canonical():
    """An ai_id that is ALREADY a full canonical 3-form (contains a dot — e.g.
    a practice that stores `empirica-foundation.carly.flta`) must NOT be
    double-prefixed. Regression for prop_mzsijy2."""
    already = "empirica-foundation.carly.flta"
    assert compose_canonical_seat(mesh_id_prefix="empirica.david", ai_id=already) == already
    # bare basename still gets the prefix
    assert compose_canonical_seat(mesh_id_prefix="empirica.david", ai_id="empirica") == "empirica.david.empirica"
    # empties still return None
    assert compose_canonical_seat(mesh_id_prefix="empirica.david", ai_id="") is None
    assert compose_canonical_seat(mesh_id_prefix="", ai_id="empirica") is None
    # already-canonical passes through even with no prefix (dot-check wins)
    assert compose_canonical_seat(mesh_id_prefix="", ai_id=already) == already

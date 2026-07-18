"""project-init --force must not clobber hand-set project.yaml fields.

Regression guard for the epistemic-dj repair bug: a --force re-init rebuilt
project.yaml from a generic scaffold, silently overwriting curated identity
fields (org_id, tenant_slug, canonical_seat, remotes, rich description).
"""

from __future__ import annotations

from empirica.cli.command_handlers.project_init import _merge_preserving_existing


def test_existing_hand_set_fields_survive():
    template = {
        "version": "2.0",
        "name": "epistemic-dj",
        "org_id": "",  # empty scaffold value that would clobber
        "description": "epistemic-dj project",
        "tags": [],
    }
    existing = {
        "org_id": "empirica",
        "tenant_slug": "david",
        "canonical_seat": "empirica.david.epistemic-dj",
        "mesh_id_prefix": "empirica.david",
        "description": "Rich curated description",
        "project_id": "748a-real-id",
    }
    merged = _merge_preserving_existing(template, existing)
    # curated identity fields preserved, not clobbered by the empty scaffold
    assert merged["org_id"] == "empirica"
    assert merged["tenant_slug"] == "david"
    assert merged["canonical_seat"] == "empirica.david.epistemic-dj"
    assert merged["mesh_id_prefix"] == "empirica.david"
    assert merged["description"] == "Rich curated description"
    assert merged["project_id"] == "748a-real-id"
    # template-only keys still fill in
    assert merged["version"] == "2.0"
    assert merged["name"] == "epistemic-dj"


def test_fresh_init_returns_template_unchanged():
    template = {"version": "2.0", "name": "x"}
    assert _merge_preserving_existing(template, {}) == template
    assert _merge_preserving_existing(template, None) == template


def test_template_fills_keys_missing_from_existing():
    template = {"a": 1, "b": 2, "c": 3}
    existing = {"a": 99}
    assert _merge_preserving_existing(template, existing) == {"a": 99, "b": 2, "c": 3}

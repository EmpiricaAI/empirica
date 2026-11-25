"""practice-context lists cross-org peers from the roster's cross_org_visible (goal f92d7fff).

The org doctrine says a practice practice-context does not return does not exist for an AI, and practice-context read only
self and org.tenants[].projects[], so every peer reachable through a tenant-tenant or org-org agreement was invisible.
"""

from __future__ import annotations

from empirica.cli.command_handlers.practice_context_commands import _project_roster_to_addressbook


def _practice(mesh, slug):
    return {"canonical_ai_id_mesh": mesh, "slug": slug, "project_id": f"p-{slug}", "display_name": slug}


ROSTER = {
    "self": {"tenant_slug": "david"},
    "org": {"slug": "empirica", "tenants": [{"tenant_slug": "david", "projects": [
        {"ai_id_short": "empirica", "ai_id_mesh": "empirica.david.empirica", "id": "p0"}]}]},
    "cross_org_visible": [
        {"scope": "tenant_tenant", "state": "active", "other_party": {"org_slug": "nle", "tenant_slug": "ana"},
         "practices": [_practice("nle.ana.research", "research")]},
        {"scope": "org_org", "state": "active", "other_party": {"org_slug": "acme"},
         "practices": [_practice("acme.bob.support", "support"), _practice("empirica.david.empirica", "empirica")]},
        {"scope": "org_org", "state": "revoked", "other_party": {"org_slug": "gone"},
         "practices": [_practice("gone.x.y", "y")]},
        {"scope": "seat_seat", "state": "active", "other_party": {"org_slug": "s1", "reachable_from": "empirica.david.empirica"},
         "practices": [_practice("s1.t.mine", "mine")]},
        {"scope": "seat_seat", "state": "active", "other_party": {"org_slug": "s2", "reachable_from": "empirica.david.other"},
         "practices": [_practice("s2.t.notmine", "notmine")]},
        {"scope": "seat_seat", "state": "active", "other_party": {"org_slug": "s3"},
         "practices": [_practice("s3.t.unscoped", "unscoped")]},
    ],
}  # fmt: skip


def _rows():
    return {r["ai_id_mesh"]: r for r in _project_roster_to_addressbook(ROSTER, "empirica")}


def test_tenant_and_org_agreements_add_their_practices_as_cross_org_peers():
    rows = _rows()
    assert rows["nle.ana.research"]["role"] == "cross_org" and rows["nle.ana.research"]["scope"] == "tenant_tenant"
    assert rows["nle.ana.research"]["org"] == "nle" and rows["nle.ana.research"]["tenant"] == "ana"
    assert rows["acme.bob.support"]["role"] == "cross_org" and rows["acme.bob.support"]["tenant"] == "bob"


def test_an_inactive_agreement_adds_nothing():
    assert "gone.x.y" not in _rows()


def test_a_seat_agreement_counts_only_from_the_practice_it_names():
    rows = _rows()
    assert "s1.t.mine" in rows
    assert "s2.t.notmine" not in rows and "s3.t.unscoped" not in rows


def test_an_own_practice_listed_in_an_agreement_is_not_duplicated():
    rows = _project_roster_to_addressbook(ROSTER, "empirica")
    assert [r["ai_id_mesh"] for r in rows].count("empirica.david.empirica") == 1


def test_a_roster_without_cross_org_rows_is_unchanged():
    roster = {k: v for k, v in ROSTER.items() if k != "cross_org_visible"}
    assert [r["role"] for r in _project_roster_to_addressbook(roster, "empirica")] == ["self"]

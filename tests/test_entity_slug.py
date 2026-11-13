"""One entity slug for core and workspace (goal 279032c1, workspace prop_dl6lmazcenebrgfputvfxxr6tu).

Core's _slugify kept Unicode letters (König -> könig) and workspace's dropped them (k-nig) and cut mid-word at 60, so
the two minted different ids for one name. entity_slug transliterates German (ae, oe, ue, ss), strips other accents through
NFKD, and cuts at a word boundary.
"""

from __future__ import annotations

import pytest

from empirica.core.entity_ids import entity_slug


@pytest.mark.parametrize(
    "text, slug",
    [
        ("Manuel König", "manuel-koenig"),
        ("München GmbH", "muenchen-gmbh"),
        ("Straße & Co.", "strasse-co"),
        ("Ärzte Übersicht", "aerzte-uebersicht"),
        ("Café Crème", "cafe-creme"),
        ("Ångström Labs", "angstroem-labs"),
        ("plain-ascii name", "plain-ascii-name"),
        ("  --Edge__case!!  ", "edge-case"),
        ("", ""),
    ],
)
def test_transliterates_and_slugs(text, slug):
    assert entity_slug(text) == slug


def test_a_long_name_is_cut_at_a_word_boundary_not_mid_word():
    name = "hawa sliding solutions via manuel koenig twelve point pdf then in person"
    slug = entity_slug(name)
    assert len(slug) <= 60 and not slug.endswith("-")
    assert name.replace(" ", "-").startswith(slug) and name.replace(" ", "-")[len(slug)] == "-"


def test_one_unbroken_word_longer_than_the_limit_is_cut_hard():
    assert entity_slug("x" * 80) == "x" * 60


def test_core_mints_ids_with_it():
    from empirica.cli.command_handlers.entity_commands import _slugify

    assert _slugify("Manuel König") == "manuel-koenig"

"""The harvest map (goal e3a0752a) covers every project type, domain and work type, names only real material kinds,
and ranks a practice's material from its type."""

from __future__ import annotations

from pathlib import Path

from empirica.core import harvest_sources as hs
from empirica.core.post_test.mapper import WORK_TYPE_RELEVANCE
from empirica.core.project_types import PROJECT_TYPES

_ROOT = Path(__file__).resolve().parent.parent


def test_every_project_type_domain_and_work_type_is_mapped():
    assert set(hs.catalog()["project_types"]) == set(PROJECT_TYPES)
    assert set(hs.catalog()["domains"]) == {p.stem for p in (_ROOT / "empirica" / "config" / "domains").glob("*.yaml")}
    assert set(hs.catalog()["work_types"]) == set(WORK_TYPE_RELEVANCE)


def test_every_kind_named_exists_and_says_what_it_needs():
    kinds = hs.kinds()
    for ptype, ranked in hs.catalog()["project_types"].items():
        assert ranked and set(ranked) <= set(kinds) and len(ranked) == len(set(ranked)), ptype
    for name, spec in kinds.items():
        assert spec["consent"] in hs.CONSENT, name
        assert spec["holds"] and spec["read"] and spec["title"], name


def test_sources_for_ranks_the_types_material_first_and_lists_the_rest():
    ranked = hs.sources_for("service", "operations")
    assert [k.name for k in ranked.primary] == ["commits", "reviews", "code", "docs", "ops_records"]
    assert {k.name for k in ranked.primary} | {k.name for k in ranked.other} == set(hs.kinds())
    assert not {k.name for k in ranked.primary} & {k.name for k in ranked.other}
    assert ranked.domain_note.startswith("runbooks")


def test_an_unknown_type_or_domain_falls_back_rather_than_failing():
    ranked = hs.sources_for("no-such-type", None)
    assert ranked.primary == () and len(ranked.other) == len(hs.kinds()) and ranked.domain_note == "no change"

"""The harvest map: which material /graph-harvest can read and which matters most for a practice (goal e3a0752a).

`empirica/config/harvest_sources.yaml` holds it; tests/test_harvest_sources.py pins that every project type, domain and
work type is mapped. `sources_for()` ranks the material kinds for one practice: its project type's kinds first, then the
rest, with the domain's adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

MAP_PATH = Path(__file__).resolve().parent.parent / "config" / "harvest_sources.yaml"

#: none: the practice's own material; practice_opt_in: a setting the subject practice turns on itself;
#: per_item_yes: someone else's material, read only with the person's yes for each item.
CONSENT = ("none", "practice_opt_in", "per_item_yes")


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    import yaml

    return yaml.safe_load(MAP_PATH.read_text(encoding="utf-8")) or {}


def kinds() -> dict[str, dict]:
    return catalog().get("kinds") or {}


@dataclass(frozen=True)
class Kind:
    name: str
    title: str
    holds: str
    read: str
    consent: str


@dataclass(frozen=True)
class Ranked:
    """A practice's material: ``primary`` holds most for its project type, most valuable first; ``other`` is the rest."""

    primary: tuple[Kind, ...]
    other: tuple[Kind, ...]
    domain_note: str


def _kind(name: str) -> Kind:
    spec = kinds()[name]
    return Kind(name, spec["title"], spec["holds"], spec["read"], spec["consent"])


def sources_for(project_type: str | None, domain: str | None) -> Ranked:
    """Rank the material kinds for a practice. An unknown project type ranks nothing first; an unknown domain adds no
    adjustment: the map advises, it never refuses a harvest."""
    first = [n for n in (catalog().get("project_types") or {}).get(project_type or "", []) if n in kinds()]
    rest = [n for n in kinds() if n not in first]
    note = (catalog().get("domains") or {}).get(domain or "default") or (catalog().get("domains") or {}).get(
        "default", ""
    )
    return Ranked(tuple(_kind(n) for n in first), tuple(_kind(n) for n in rest), note)


def when_it_pays(work_type: str | None) -> str | None:
    return (catalog().get("work_types") or {}).get(work_type or "")

"""The one slug for entity ids, shared by core and empirica-workspace (goal 279032c1).

Organization, engagement and contact ids are minted from names in two repositories that must agree: core's mint_entity
and mint_contact, and workspace's CRM commands. They did not: core kept Unicode letters (König -> könig), workspace
dropped them (k-nig) and cut mid-word. This is the single definition both import:

- German letters are transliterated (ä ae, ö oe, ü ue, ß ss), so König and Koenig mint the same id;
- other accents are stripped through NFKD (Café -> cafe);
- anything not a-z or 0-9 becomes one hyphen, trimmed at both ends;
- a slug longer than ``max_len`` is cut at the last hyphen inside the limit, or hard at the limit for one long word.

Ids already minted stay as they are: they are join targets. Measured 2026-11-13: no organization or engagement id was
non-ASCII; three contact ids were, and contacts deduplicate by email.
"""

from __future__ import annotations

import re
import unicodedata

MAX_LEN = 60

_GERMAN = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "ẞ": "ss"})
_NON_SLUG = re.compile(r"[^a-z0-9]+")


def entity_slug(text: str, max_len: int = MAX_LEN) -> str:
    """The id slug for an entity name. Pure and deterministic: the same name gives the same slug in every repository."""
    lowered = (text or "").lower().translate(_GERMAN)
    ascii_text = unicodedata.normalize("NFKD", lowered).encode("ascii", "ignore").decode("ascii")
    slug = _NON_SLUG.sub("-", ascii_text).strip("-")
    if len(slug) <= max_len:
        return slug
    cut = slug[:max_len]
    boundary = cut.rfind("-")
    return (cut[:boundary] if boundary > 0 else cut).strip("-")

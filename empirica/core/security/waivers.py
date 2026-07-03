"""Governed CVE-waiver source — the single list both the release gate
(``scripts/release.py``) and ``empirica security-audit`` honor, so they can't
drift.

A waiver is a documented, reviewed **risk-acceptance** for a CVE that is (a)
assessed NON-EXPLOITABLE in Empirica's usage, AND (b) has no available fix. Each
entry MUST carry an ``id``, ``package``, ``rationale``, and ``retire_when``. Add
an ``aliases`` list when the same vuln is tracked under multiple ids
(PYSEC / GHSA / CVE) so a match on any alias waives it.

Currently **EMPTY**: the sole prior waiver (PYSEC-2026-597 / CVE-2026-12243,
nltk via textstat) was RETIRED by dropping textstat for the in-house readability
module (#212) — nltk is gone from the tree, so the CVE is gone rather than
waived. Both gates are STRICT by default: any un-waived empirica CVE fails, and
``security-audit`` never waives a CISA-KEV (actively-exploited) match.
"""

from __future__ import annotations

from collections.abc import Iterable

# Each entry: {"id": str, "package": str, "rationale": str, "retire_when": str,
#              "aliases"?: list[str]}
CVE_WAIVERS: list[dict] = []


def waived_ids() -> set[str]:
    """Every id + alias across the governed waiver list (a flat lookup set)."""
    ids: set[str] = set()
    for w in CVE_WAIVERS:
        wid = w.get("id")
        if wid:
            ids.add(wid)
        ids.update(a for a in (w.get("aliases") or []) if a)
    return ids


def is_waived(finding_ids: Iterable[str]) -> bool:
    """True iff any of a finding's ids (vulnerability_id / aliases / CVE ids) is
    in the governed waiver list."""
    waived = waived_ids()
    return any(fid in waived for fid in finding_ids if fid)

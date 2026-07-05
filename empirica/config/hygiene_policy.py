"""Per-practice artifact-hygiene policy resolution (artifact-hygiene WS2).

Reads the ``hygiene_policy`` block from ``.empirica/project.yaml`` over a single
source-of-truth default set — mirroring the artifact-graph gate's scalar resolver
(``_resolve_gate_scalars``, #253): a host/extension config owns these values; the
resolver clamps + validates and **never raises** (a bad policy must not break a
hygiene sweep). The policy governs how aggressively each practice cleans:
research keeps unknowns open longer (they *are* the work), code closes
evidence-backed goals fast, outreach watches link-rot. See
``docs/architecture/ARTIFACT_HYGIENE.md`` §4.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Single source of truth: the default for every policy field. Numeric fields
# clamp to >= 0; enum fields validate against their allowed set (unknown value
# → default). Keep this the ONLY place defaults live.
HYGIENE_POLICY_DEFAULTS: dict = {
    "source_staleness_days": 30,  # re-probe sources older than this; 0 = probe all
    "unknown_triage_days": 14,  # flag unknowns open longer than this for triage
    "goal_auto_close": "evidence_only",  # evidence_only | surface_only
    "auto_delete": "test_noise_only",  # off | test_noise_only  (never semantic-on-age)
    "dedup": "exact_only",  # exact_only | fuzzy
}

_ENUM_FIELDS: dict = {
    "goal_auto_close": {"evidence_only", "surface_only"},
    "auto_delete": {"off", "test_noise_only"},
    "dedup": {"exact_only", "fuzzy"},
}
_INT_FIELDS = frozenset({"source_staleness_days", "unknown_triage_days"})


def _coerce(field: str, value) -> object:
    """Coerce + validate one policy field; return its default on anything off."""
    default = HYGIENE_POLICY_DEFAULTS[field]
    if field in _INT_FIELDS:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return n if n >= 0 else default
    if field in _ENUM_FIELDS:
        return value if value in _ENUM_FIELDS[field] else default
    return default


def _find_project_root() -> Path | None:
    """Walk up from cwd for a directory containing ``.empirica/project.yaml``."""
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".empirica" / "project.yaml").exists():
            return parent
    return None


def resolve_hygiene_policy(project_root: Path | str | None = None) -> dict:
    """Resolve the per-practice hygiene policy.

    Defaults overlaid with the ``.empirica/project.yaml`` ``hygiene_policy``
    block. Each supplied field is coerced/validated; anything invalid falls back
    to its default. Never raises — a missing/unparseable project.yaml yields the
    pure defaults.
    """
    policy = dict(HYGIENE_POLICY_DEFAULTS)
    try:
        import yaml

        root = Path(project_root) if project_root else _find_project_root()
        if root is None:
            return policy
        proj_yaml = Path(root) / ".empirica" / "project.yaml"
        if not proj_yaml.exists():
            return policy
        data = yaml.safe_load(proj_yaml.read_text(encoding="utf-8")) or {}
        block = data.get("hygiene_policy")
        if isinstance(block, dict):
            for field in HYGIENE_POLICY_DEFAULTS:
                if field in block:
                    policy[field] = _coerce(field, block[field])
    except Exception as e:
        # A bad policy / unreadable yaml must not break a hygiene sweep — but
        # surface it at debug so a mis-set policy is diagnosable, not silent.
        logger.debug("hygiene_policy: resolve failed, using defaults (%s)", e)
    return policy

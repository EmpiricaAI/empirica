"""Calibration config — the user-settable epistemic weights + Sentinel thresholds.

A thin **overlay resolver** over empirica's existing config systems. It declares
the tunable surface (4 dimension weights + 4 Sentinel thresholds — the same shape
personas already use as ``EpistemicConfig``) and resolves an effective config by
layering, low→high precedence::

    base defaults → persona preset → global override → practice override

Each override layer is a **sparse** dict (only the keys a scope actually changed),
optionally carrying a ``preset`` naming a built-in persona template. Storage is a
dedicated ``.empirica/calibration.yaml`` per scope (practice = ``<project>/.empirica``,
global = ``~/.empirica``) — deliberately NOT embedded in ``project.yaml``, whose
comments/canonical fields we must not rewrite.

The extension's "Sentinel Tuning" tab reads/writes this via the daemon
(``GET/PATCH /api/v1/calibration/config``).

Scope note: this layer is the single *settable source* + read/write surface. It
does not yet change runtime enforcement — migrating the scattered gate checks to
read ``resolve()`` is a tracked follow-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class FieldSpec:
    """One tunable field: its group, default, valid range, and display label."""

    key: str
    group: str  # "weights" | "thresholds"
    default: float
    minimum: float
    maximum: float
    label: str
    is_gate: bool = False


# The settable surface — mirrors persona EpistemicConfig (weights + thresholds).
# Defaults match the current base config (reflex_exporter overall weights +
# persona/UniversalConstraints threshold defaults). Extensible: add rows here
# (e.g. comprehension.high) as the tuning surface grows.
SCHEMA: tuple[FieldSpec, ...] = (
    # Dimension weights — a sum-to-1 group (the confidence-rollup weighting).
    FieldSpec("foundation", "weights", 0.35, 0.0, 1.0, "Foundation"),
    FieldSpec("comprehension", "weights", 0.25, 0.0, 1.0, "Comprehension"),
    FieldSpec("execution", "weights", 0.25, 0.0, 1.0, "Execution"),
    FieldSpec("engagement", "weights", 0.15, 0.0, 1.0, "Engagement"),
    # Sentinel thresholds.
    FieldSpec("engagement_gate", "thresholds", 0.60, 0.0, 1.0, "Engagement gate", is_gate=True),
    FieldSpec("uncertainty_trigger", "thresholds", 0.40, 0.0, 1.0, "Uncertainty trigger"),
    FieldSpec("confidence_to_proceed", "thresholds", 0.75, 0.0, 1.0, "Confidence to proceed"),
    FieldSpec("signal_quality_min", "thresholds", 0.60, 0.0, 1.0, "Signal quality min"),
)

GROUPS: tuple[str, ...] = ("weights", "thresholds")
WEIGHT_KEYS: tuple[str, ...] = tuple(f.key for f in SCHEMA if f.group == "weights")
_SPEC_BY_GROUP_KEY: dict[tuple[str, str], FieldSpec] = {(f.group, f.key): f for f in SCHEMA}

_CALIBRATION_FILENAME = "calibration.yaml"


# ── schema helpers ───────────────────────────────────────────────────────────


def schema_json() -> list[dict[str, Any]]:
    """The schema as JSON-serializable field specs (for the extension UI)."""
    return [
        {
            "key": f.key,
            "group": f.group,
            "default": f.default,
            "min": f.minimum,
            "max": f.maximum,
            "label": f.label,
            "is_gate": f.is_gate,
        }
        for f in SCHEMA
    ]


def default_config() -> dict[str, dict[str, float]]:
    """The base config from SCHEMA defaults, grouped by weights/thresholds."""
    out: dict[str, dict[str, float]] = {g: {} for g in GROUPS}
    for f in SCHEMA:
        out[f.group][f.key] = f.default
    return out


def preset_names() -> set[str]:
    """Names of the built-in persona presets (empty set if unavailable)."""
    try:
        from empirica.core.persona.templates import BUILTIN_TEMPLATES

        return set(BUILTIN_TEMPLATES)
    except Exception:
        return set()


def _preset_layer(name: str | None) -> dict[str, dict[str, float]] | None:
    """Return {weights, thresholds} for a persona preset, or None if unknown."""
    if not name:
        return None
    try:
        from empirica.core.persona.templates import BUILTIN_TEMPLATES
    except Exception:
        return None
    tpl = BUILTIN_TEMPLATES.get(name)
    if not isinstance(tpl, dict):
        return None
    layer: dict[str, dict[str, float]] = {"weights": {}, "thresholds": {}}
    for group in ("weights", "thresholds"):
        block = tpl.get(group)
        if not isinstance(block, dict):
            continue
        for k, v in block.items():
            try:
                layer[group][str(k)] = float(v)
            except (TypeError, ValueError):
                continue
    return layer


def _clamp(spec: FieldSpec, value: Any) -> float | None:
    """Coerce+clamp a value to the spec's range, or None if not a number."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return max(spec.minimum, min(spec.maximum, v))


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Normalize the weight group to sum to 1.0. Falls back to equal weights if
    the sum is non-positive."""
    total = sum(float(weights.get(k, 0.0)) for k in WEIGHT_KEYS)
    if total <= 0:
        return {k: 1.0 / len(WEIGHT_KEYS) for k in WEIGHT_KEYS}
    return {k: float(weights.get(k, 0.0)) / total for k in WEIGHT_KEYS}


def _apply_overlay(
    resolved: dict[str, dict[str, float]],
    source_map: dict[tuple[str, str], str],
    overlay: dict[str, dict[str, float]] | None,
    source_label: str,
) -> None:
    """Overlay a sparse {weights, thresholds} dict onto resolved, clamping to
    spec range and stamping source_map. Unknown keys are ignored."""
    if not overlay:
        return
    for group in GROUPS:
        for key, value in (overlay.get(group) or {}).items():
            spec = _SPEC_BY_GROUP_KEY.get((group, key))
            if spec is None:
                continue
            clamped = _clamp(spec, value)
            if clamped is None:
                continue
            resolved[group][key] = clamped
            source_map[(group, key)] = source_label


def resolve(
    global_override: dict | None = None,
    practice_override: dict | None = None,
    normalize: bool = True,
) -> dict[str, Any]:
    """Resolve the effective config by layering base → global → practice.

    Each ``*_override`` is a sparse dict, optionally with a ``preset`` key naming
    a persona template. Within a scope, the preset applies first, then the
    scope's per-key overrides. Returns::

        {
          "weights": {...}, "thresholds": {...},
          "preset": <effective preset name or None>,
          "sources": {"<group>.<key>": "default|preset:<name>|global|practice"},
          "overridden": ["<group>.<key>", ...],   # keys set above the default
        }
    """
    resolved = default_config()
    source_map: dict[tuple[str, str], str] = {(f.group, f.key): "default" for f in SCHEMA}
    effective_preset: str | None = None

    for scope_label, override in (("global", global_override), ("practice", practice_override)):
        override = override or {}
        preset_name = override.get("preset")
        preset = _preset_layer(preset_name)
        if preset:
            effective_preset = preset_name
            _apply_overlay(resolved, source_map, preset, f"preset:{preset_name}")
        sparse = {g: (override.get(g) or {}) for g in GROUPS}
        _apply_overlay(resolved, source_map, sparse, scope_label)

    if normalize:
        resolved["weights"] = normalize_weights(resolved["weights"])

    sources = {f"{g}.{k}": src for (g, k), src in source_map.items()}
    overridden = sorted(f"{g}.{k}" for (g, k), src in source_map.items() if src != "default")
    return {
        "weights": resolved["weights"],
        "thresholds": resolved["thresholds"],
        "preset": effective_preset,
        "sources": sources,
        "overridden": overridden,
    }


# ── sparse-override store (dedicated .empirica/calibration.yaml per scope) ─────


def override_path(scope_dir: str | Path) -> Path:
    """The calibration override file inside a scope's .empirica dir."""
    return Path(scope_dir) / ".empirica" / _CALIBRATION_FILENAME


def read_override(scope_dir: str | Path) -> dict[str, Any]:
    """Read a scope's sparse override ({} if absent/unreadable)."""
    p = override_path(scope_dir)
    if not p.exists():
        return {}
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def validate_patch(patch: dict) -> tuple[dict, list[str]]:
    """Validate a sparse PATCH body ({weights?, thresholds?, preset?}). Returns
    (clean, errors). Clamps numbers to spec range; a ``None`` value is a valid
    reset-to-default signal; unknown keys/presets are errors."""
    clean: dict[str, Any] = {}
    errors: list[str] = []

    if "preset" in patch:
        name = patch["preset"]
        if name is None or (isinstance(name, str) and name in preset_names()):
            clean["preset"] = name
        else:
            errors.append(f"unknown preset: {name!r}")

    for group in GROUPS:
        block = patch.get(group)
        if block is None:
            continue
        if not isinstance(block, dict):
            errors.append(f"{group} must be an object")
            continue
        for key, value in block.items():
            spec = _SPEC_BY_GROUP_KEY.get((group, key))
            if spec is None:
                errors.append(f"unknown {group} key: {key}")
                continue
            if value is None:
                clean.setdefault(group, {})[key] = None  # reset-to-default
                continue
            clamped = _clamp(spec, value)
            if clamped is None:
                errors.append(f"{group}.{key} must be a number in [{spec.minimum}, {spec.maximum}]")
                continue
            clean.setdefault(group, {})[key] = clamped

    return clean, errors


def apply_patch(scope_dir: str | Path, patch: dict) -> dict[str, Any]:
    """Merge a validated sparse patch into a scope's override file (creating it
    if absent). A ``None`` value removes that key (reset-to-default). Returns the
    resulting override block. Callers should ``validate_patch`` first."""
    p = override_path(scope_dir)
    block = read_override(scope_dir)

    if "preset" in patch:
        if patch["preset"] is None:
            block.pop("preset", None)
        else:
            block["preset"] = patch["preset"]

    for group in GROUPS:
        if group not in patch:
            continue
        gblock = block.get(group)
        if not isinstance(gblock, dict):
            gblock = {}
        for key, value in patch[group].items():
            if value is None:
                gblock.pop(key, None)
            else:
                gblock[key] = value
        if gblock:
            block[group] = gblock
        else:
            block.pop(group, None)

    p.parent.mkdir(parents=True, exist_ok=True)
    if block:
        p.write_text(yaml.safe_dump(block, default_flow_style=False, sort_keys=False), encoding="utf-8")
    elif p.exists():
        p.unlink()  # nothing left to override → remove the file
    return block

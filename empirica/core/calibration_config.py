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

Runtime enforcement: the live CHECK gate (uncertainty-only, per the 2026-04-07
redesign) and the Sentinel hook's engagement escalate read
``override_thresholds()`` — a practice/global ``ready_uncertainty`` or
``engagement_gate`` override shifts the gate live, fail-safe (a bad/missing file
never widens it) with the Brier overconfidence-floor still tightening on top.
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
    # Sentinel thresholds. ready_uncertainty is THE live CHECK gate
    # (proceed when uncertainty <= this; default 0.35, per the 2026-04-07
    # meta-uncertainty redesign). engagement_gate feeds the hook's escalate
    # check; the rest feed personas/reports.
    FieldSpec(
        "ready_uncertainty", "thresholds", 0.35, 0.0, 1.0, "CHECK gate: max uncertainty to proceed", is_gate=True
    ),
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


# ── calibration-stance presets (the orthogonal axis to domain personas) ──────
# STANCE = how strictly the practice gates (owns the two is_gate thresholds:
# ready_uncertainty + engagement_gate). PERSONA (BUILTIN_TEMPLATES) = what the
# practice focuses on (owns weights + the soft thresholds). The key partitions
# don't overlap, so the two axes compose cleanly (extension prop_aablfzw5).
# ready_uncertainty is the live CHECK gate: LOWER = stricter (proceed only at
# lower uncertainty). 'balanced' == SCHEMA defaults (the neutral baseline).
STANCE_PRESETS: dict[str, dict[str, dict[str, float]]] = {
    "rigorous": {"thresholds": {"ready_uncertainty": 0.25, "engagement_gate": 0.80}},
    "balanced": {"thresholds": {"ready_uncertainty": 0.35, "engagement_gate": 0.60}},
    "exploratory": {"thresholds": {"ready_uncertainty": 0.45, "engagement_gate": 0.50}},
}


def stance_names() -> set[str]:
    """Names of the built-in calibration-stance presets."""
    return set(STANCE_PRESETS)


def _stance_layer(name: str | None) -> dict[str, dict[str, float]] | None:
    """Return {thresholds: {...}} for a stance preset, or None if unknown."""
    if not name:
        return None
    tpl = STANCE_PRESETS.get(name)
    if not isinstance(tpl, dict):
        return None
    layer: dict[str, dict[str, float]] = {"weights": {}, "thresholds": {}}
    for group in ("weights", "thresholds"):
        block = tpl.get(group)
        if isinstance(block, dict):
            layer[group] = {str(k): float(v) for k, v in block.items()}
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
          "preset": <effective persona preset name or None>,
          "stance": <effective calibration-stance preset name or None>,
          "sources": {"<group>.<key>": "default|preset:<name>|stance:<name>|global|practice"},
          "overridden": ["<group>.<key>", ...],   # keys set above the default
        }
    """
    resolved = default_config()
    source_map: dict[tuple[str, str], str] = {(f.group, f.key): "default" for f in SCHEMA}
    effective_preset: str | None = None
    effective_stance: str | None = None

    for scope_label, override in (("global", global_override), ("practice", practice_override)):
        override = override or {}
        # Two orthogonal preset axes, then the scope's per-key sparse override
        # (which always wins). Persona owns weights + soft thresholds; stance
        # owns the gate thresholds — non-overlapping, so axis order is immaterial.
        preset_name = override.get("preset")
        preset = _preset_layer(preset_name)
        if preset:
            effective_preset = preset_name
            _apply_overlay(resolved, source_map, preset, f"preset:{preset_name}")
        stance_name = override.get("stance")
        stance = _stance_layer(stance_name)
        if stance:
            effective_stance = stance_name
            _apply_overlay(resolved, source_map, stance, f"stance:{stance_name}")
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
        "stance": effective_stance,
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

    if "stance" in patch:
        name = patch["stance"]
        if name is None or (isinstance(name, str) and name in stance_names()):
            clean["stance"] = name
        else:
            errors.append(f"unknown stance: {name!r}")

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

    if "stance" in patch:
        if patch["stance"] is None:
            block.pop("stance", None)
        else:
            block["stance"] = patch["stance"]

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


# ── runtime resolution (entry point for enforcement wiring) ──────────────────


def effective_for_session(project_path: str | Path | None = None) -> dict[str, Any]:
    """Resolve the effective calibration config for the active practice.

    Layers global (``~/.empirica``) → practice (``<project_path>/.empirica``).
    ``project_path=None`` resolves global-only. Returns the same shape as
    ``resolve()``.

    This is the runtime entry point for enforcement sites: the caller passes the
    current session's project dir (which enforcement sites already resolve for
    other reasons), so this module stays a leaf and never imports the session
    resolver. Enforcement sites should read a value only when its key is in the
    returned ``overridden`` list — absent an override the resolved value equals
    the base default, so a bad/missing file can never widen a gate.
    """
    global_override = read_override(Path.home())
    practice_override = read_override(project_path) if project_path else {}
    return resolve(global_override, practice_override)


def override_thresholds(project_path: str | Path | None = None) -> dict[str, float]:
    """Return ONLY the threshold keys explicitly overridden (practice → global)
    for the active practice, as ``{key: value}``.

    Empty dict on no-override OR any error — **fail-safe by construction**, so a
    missing or malformed ``calibration.yaml`` can never change a default. This is
    the entry point for enforcement sites: they call it and honor only the keys
    present, leaving their hardcoded default in place for everything else. The
    returned value is the settable *base* — enforcement layers (Brier, env) still
    apply on top.
    """
    try:
        cfg = effective_for_session(project_path)
        out: dict[str, float] = {}
        for dotted in cfg.get("overridden", []):
            if not dotted.startswith("thresholds."):
                continue
            key = dotted.split(".", 1)[1]
            val = cfg["thresholds"].get(key)
            if isinstance(val, (int, float)):
                out[key] = float(val)
        return out
    except Exception:
        return {}

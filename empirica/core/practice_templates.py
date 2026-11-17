"""Practice templates and cockpit presets for onboarding (goal e8c47635, David 2026-11-17).

`empirica/config/practice_templates.yaml` holds the catalog. `suggest()` turns the interview's workload answers into
candidate practices, each a prediction with a confidence and the reason it rests on, which /ewm-interview routes the way
the forecast skill routes directions: high confidence is confirmed with one tap, low confidence is asked about or parked.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "practice_templates.yaml"


@lru_cache(maxsize=1)
def catalog() -> dict[str, Any]:
    import yaml

    return yaml.safe_load(CATALOG_PATH.read_text(encoding="utf-8")) or {}


def templates() -> dict[str, dict]:
    return catalog().get("templates") or {}


def presets() -> dict[str, dict]:
    return catalog().get("presets") or {}


@dataclass(frozen=True)
class Candidate:
    """A practice the person's answers suggest: a prediction, with what grounds it."""

    template: str
    confidence: float
    reason: str


def suggest(workloads: dict[str, str]) -> list[Candidate]:
    """Candidate practices from workload answers ``{work kind: how often}`` (often / sometimes / rarely), most confident
    first. A work kind done often grounds its template strongly; sometimes, moderately; rarely, not enough to propose it
    (it is parked by the interview). Support is proposed for everyone, and a coordinator once there are two or more other
    practices to coordinate."""
    weight = {"often": 0.85, "sometimes": 0.6, "rarely": 0.3}
    labels = catalog().get("workloads") or {}
    found: dict[str, Candidate] = {}
    for name, spec in templates().items():
        for kind in spec.get("suggested_by") or []:
            if kind in workloads:
                confidence = weight.get(workloads[kind], 0.3)
                reason = f"you said {labels.get(kind, kind).lower()}: {workloads[kind]}"
                if name not in found or confidence > found[name].confidence:
                    found[name] = Candidate(name, confidence, reason)
    others = [c for c in found.values() if c.confidence >= 0.6]
    if len(others) >= 2:
        found["coordinator"] = Candidate("coordinator", 0.7, f"{len(others)} practices to coordinate")
    found["support"] = Candidate("support", 0.8, "every setup needs its machine and tools looked after")
    return sorted(found.values(), key=lambda c: -c.confidence)


def route(candidate: Candidate) -> str:
    """The forecast skill's routing for a practice: 'confirm' (one tap), 'ask' (one question), or 'park' (add later)."""
    if candidate.confidence >= 0.7:
        return "confirm"
    if candidate.confidence >= 0.5:
        return "ask"
    return "park"

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


def template_provides(type_: str | None, domain: str | None) -> list[str]:
    """The default ``provides`` of the template whose (type, domain) is exactly this practice's, else []."""
    if not (type_ and domain):
        return []
    for spec in templates().values():
        if isinstance(spec, dict) and spec.get("type") == type_ and spec.get("domain") == domain:
            return [str(d) for d in spec.get("provides") or []]
    return []


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
    (it is parked by the interview). Support is proposed for everyone, and a coordinator once two or more confirmed
    practices, grounded in two or more different answers, need coordinating. Ties keep the catalog's order, which lists
    the person's own work before research. An unknown work kind or frequency raises ValueError rather than being read as
    'rarely'."""
    weight = {"often": 0.85, "sometimes": 0.6, "rarely": 0.3}
    labels = catalog().get("workloads") or {}
    unknown = sorted(k for k in workloads if k not in labels)
    if unknown:
        raise ValueError(f"unknown work kind {', '.join(unknown)}; known: {', '.join(labels)}")
    bad = sorted(f"{k}={v}" for k, v in workloads.items() if v not in weight)
    if bad:
        raise ValueError(f"unknown frequency {', '.join(bad)}; use one of {', '.join(weight)}")
    found: dict[str, Candidate] = {}
    for name, spec in templates().items():
        for kind in spec.get("suggested_by") or []:
            if kind in workloads:
                confidence = weight[workloads[kind]]
                reason = f"you said {labels.get(kind, kind).lower()}: {workloads[kind]}"
                if name not in found or confidence > found[name].confidence:
                    found[name] = Candidate(name, confidence, reason)
    confirmed = [c for c in found.values() if route(c) == "confirm"]
    if len(confirmed) >= 2 and len({c.reason for c in confirmed}) >= 2:
        found["coordinator"] = Candidate("coordinator", 0.7, f"{len(confirmed)} practices to coordinate")
    found["support"] = Candidate("support", 0.8, "every setup needs its machine and tools looked after")
    return sorted(found.values(), key=lambda c: -c.confidence)


def route(candidate: Candidate) -> str:
    """The forecast skill's routing for a practice: 'confirm' (one tap), 'ask' (one question), or 'park' (add later)."""
    if candidate.confidence >= 0.7:
        return "confirm"
    if candidate.confidence >= 0.5:
        return "ask"
    return "park"


@dataclass(frozen=True)
class StarterPlan:
    """The first plan a practice gets when onboarding ends (goal 88150eae): one goal and its tasks."""

    objective: str
    tasks: tuple[str, ...]


def starter_plan(template: str, focus: str = "") -> StarterPlan | None:
    """The template's starter, with ``{focus}`` (a repo, a client, a question from the person's answers) filled in."""
    spec = (templates().get(template) or {}).get("starter")
    if not spec:
        return None
    fill = focus.strip() or "your first piece of work"
    return StarterPlan(
        spec["objective"].replace("{focus}", fill), tuple(t.replace("{focus}", fill) for t in spec["tasks"])
    )


def main_practice(candidates: list[Candidate]) -> Candidate | None:
    """The practice that gets the first plan: the most confident one that is confirmed and is not support (support's
    work is the machine, not the person's). None when nothing else was confirmed."""
    return next((c for c in candidates if c.template != "support" and route(c) == "confirm"), None)

"""The bootstrap contract as the text a session starts with, under a token budget (goal 2e686e79, agreed with ecodex).

`project-bootstrap` returns goals, findings, unknowns, decisions, assumptions, mistakes with their prevention, dead ends, the
links among them, the blindspot telemetry and a coverage entry per category. SessionStart rendered three goals and five
findings and unknowns at 100 characters with no ids, and printed "nothing retrieved" for an empty project and for a capped
or unavailable source alike.

One selection pass: categories take turns, one item each, in priority order, until the next item would pass the budget.
Tokens are counted as ceil(bytes / 4), as codex counts them. The coverage line always renders and says, per category, what
was shown out of what exists, or why nothing is: none logged, not returned by this core, or a source that did not answer.
Pure: no I/O, so both hooks can call it and tests can feed it any history.
"""

from __future__ import annotations

import math
import os

#: ceil(bytes / 4): the count codex uses, so a budget means the same thing in both harnesses.
DEFAULT_TOKENS = 1800
ITEM_CHARS = 160

#: (bootstrap key, heading, how an item reads). Priority order: the turn order of the selection pass.
SECTIONS: tuple[tuple[str, str], ...] = (
    ("goals", "Goals"),
    ("findings", "Findings"),
    ("unknowns", "Unknowns"),
    ("decisions", "Decisions"),
    ("assumptions", "Assumptions"),
    ("mistakes_to_avoid", "Mistakes to avoid"),
    ("dead_ends", "Dead ends"),
    ("artifact_edges", "Links"),
)


def tokens(text: str) -> int:
    return math.ceil(len(text.encode("utf-8")) / 4)


def budget_from_env(default: int = DEFAULT_TOKENS) -> int:
    try:
        value = int(os.environ.get("EMPIRICA_HOOK_CONTEXT_TOKENS", "") or default)
    except ValueError:
        return default
    return max(value, 200)


def _short(text: object, limit: int = ITEM_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _id(item: dict) -> str:
    raw = item.get("id") or item.get("goal_id") or ""
    return f"[{str(raw)[:8]}] " if raw else ""


def _line(key: str, item: object) -> str:
    if not isinstance(item, dict):
        return f"  - {_short(item)}"
    if key == "goals":
        status = item.get("status")
        return f"  - {_id(item)}{_short(item.get('objective'))}" + (f" ({status})" if status else "")
    if key == "findings":
        impact = item.get("impact")
        tail = f" (impact {impact:.2g})" if isinstance(impact, (int, float)) else ""
        return f"  - {_id(item)}{_short(item.get('finding'))}{tail}"
    if key == "unknowns":
        return f"  - {_id(item)}{_short(item.get('unknown'))}"
    if key == "decisions":
        return f"  - {_id(item)}{_short(item.get('choice'))}"
    if key == "assumptions":
        conf = item.get("confidence")
        tail = f" (confidence {conf:.2g})" if isinstance(conf, (int, float)) else ""
        return f"  - {_id(item)}{_short(item.get('assumption'))}{tail}"
    if key == "mistakes_to_avoid":
        prevention = item.get("prevention")
        return f"  - {_short(item.get('mistake'), 100)}" + (f" → {_short(prevention, 120)}" if prevention else "")
    if key == "dead_ends":
        why = str(item.get("why_failed") or "").strip().splitlines()
        return f"  - {_id(item)}{_short(item.get('approach'), 100)}" + (f" ({_short(why[0], 60)})" if why else "")
    if key == "artifact_edges":
        return (
            f"  - {item.get('from_type', '?')} {str(item.get('from', ''))[:8]} —{item.get('relation', '?')}→ "
            f"{item.get('to_type', '?')} {str(item.get('to', ''))[:8]}"
        )
    return f"  - {_short(item)}"


def _coverage(breadcrumbs: dict, rendered: dict[str, int]) -> str:
    cover = breadcrumbs.get("coverage") or {}
    parts = []
    for key, heading in SECTIONS:
        name = heading.lower()
        if key not in breadcrumbs:
            parts.append(f"{name}: not returned by this core")
            continue
        entry = cover.get(key) or {}
        source = entry.get("source")
        returned = len(breadcrumbs.get(key) or [])
        total = entry.get("total")
        if source and source not in ("sqlite", "qdrant", "git"):
            parts.append(f"{name}: {source}")  # e.g. unavailable: say so, never "none"
            continue
        if returned == 0:
            parts.append(f"{name}: none" if not entry.get("truncated") else f"{name}: none returned (capped)")
            continue
        of = f"{total}" if isinstance(total, int) else (f"{returned}+" if entry.get("truncated") else f"{returned}")
        parts.append(f"{name} {rendered.get(key, 0)} of {of}")
    return "Coverage: " + "; ".join(parts)


def _blindspots(breadcrumbs: dict) -> str | None:
    status = breadcrumbs.get("blindspot_status")
    if not isinstance(status, dict):
        return None
    if status.get("error"):
        return f"Blindspots: not read ({status['error']})"
    counts = ", ".join(
        f"{status[k]} {k}" for k in ("surfaced", "acknowledged", "dismissed", "regretted") if status.get(k)
    )
    note = status.get("note")
    return "Blindspots: " + (counts or "none recorded") + (f" ({note})" if note else "")


def render(
    breadcrumbs: dict | None, *, budget: int | None = None, head: list[str] | None = None, only: set[str] | None = None
) -> str:
    """The contract as text. ``head`` (the Due section, say) renders first and counts against the budget. ``only`` limits the
    item sections to those keys (post-compact ranks findings, unknowns, dead ends and goals itself); coverage still names
    every category."""
    if not isinstance(breadcrumbs, dict):
        return "  (No context available: project-bootstrap did not answer)"
    limit = budget if budget is not None else budget_from_env()
    lines: list[str] = list(head or [])
    fixed = [
        _coverage(breadcrumbs, {}),
        _blindspots(breadcrumbs) or "",
        "Ids are 8-character prefixes; artifact verbs take them.",
    ]
    spent = tokens("\n".join(lines + fixed))

    queues = {
        key: [_line(key, i) for i in (breadcrumbs.get(key) or [])] if only is None or key in only else []
        for key, _ in SECTIONS
    }
    chosen: dict[str, list[str]] = {key: [] for key, _ in SECTIONS}
    headed: set[str] = set()
    progressed = True
    while progressed:
        progressed = False
        for key, heading in SECTIONS:
            if not queues[key]:
                continue
            cost = tokens(queues[key][0]) + (0 if key in headed else tokens(f"**{heading}:**"))
            if spent + cost > limit:
                queues[key] = []  # this category is done; smaller ones may still fit
                continue
            chosen[key].append(queues[key].pop(0))
            headed.add(key)
            spent += cost
            progressed = True

    for key, heading in SECTIONS:
        if chosen[key]:
            lines.append(f"**{heading}:**")
            lines += chosen[key]
    if not any(chosen.values()) and only is None:
        # Loaded, and nothing to show: not the same statement as "no context" (bootstrap did not answer, above).
        lines.append("  (Project loaded; nothing retrieved: the coverage line says why, per category)")
    lines.append(_coverage(breadcrumbs, {k: len(v) for k, v in chosen.items()}))
    blind = _blindspots(breadcrumbs)
    if blind:
        lines.append(blind)
    lines.append("Ids are 8-character prefixes; artifact verbs take them.")
    return "\n".join(lines)

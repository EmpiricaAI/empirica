"""Export empirica's epistemic graph as an Open Knowledge Format (OKF) bundle.

OKF (Google Cloud, 2026 — github.com/GoogleCloudPlatform/knowledge-catalog) is a
directory of markdown files with YAML frontmatter, one file per *concept*, linked
by markdown links into a graph. The only REQUIRED frontmatter field is ``type``;
``title``/``description``/``tags``/``timestamp`` are recommended, and producers
MAY add any other keys. A concept's ID is its file path within the bundle minus
the ``.md`` suffix; ``index.md`` is the reserved directory listing.

This is the "knowledge out" half of Empirica's thesis: the epistemic graph other
agents and tools can consume without an Empirica account — the file system is the
API. What OKF deliberately omits — calibration, confidence, verification — stays
Empirica's layer, and rides across as frontmatter EXTENSION keys
(``empirica_impact``, ``empirica_confidence``, ``empirica_epistemic_source``, …):
OKF-aware consumers that understand them get the calibrated graph; plain OKF
consumers ignore the extra keys and still get a valid bundle.

The reader half reuses ``artifacts_commands._read_all_notes`` (git notes are the
canonical artifact store), so this module adds only the OKF *projection*.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


# Each empirica artifact namespace → its OKF concept `type` + how to pull the
# headline text and body from the note payload. Namespaces absent from a repo
# simply yield no concepts (the reader returns []), so this list is a superset.
ARTIFACT_SPECS: tuple[dict[str, Any], ...] = (
    {"ns": "findings", "type": "Finding", "title_keys": ("finding",), "body_keys": ("description",)},
    {"ns": "unknowns", "type": "Unknown", "title_keys": ("unknown",), "body_keys": ("description", "resolved_by")},
    {
        "ns": "dead_ends",
        "type": "Dead End",
        "title_keys": ("approach",),
        "body_keys": ("why_failed", "description"),
    },
    {
        "ns": "mistakes",
        "type": "Mistake",
        "title_keys": ("mistake",),
        "body_keys": ("why_wrong", "prevention", "description"),
    },
    {
        "ns": "decisions",
        "type": "Decision",
        "title_keys": ("choice", "decision"),
        "body_keys": ("rationale", "description"),
    },
    {
        "ns": "assumptions",
        "type": "Assumption",
        "title_keys": ("assumption",),
        "body_keys": ("description",),
    },
    {"ns": "goals", "type": "Goal", "title_keys": ("objective",), "body_keys": ("description",)},
)

# Frontmatter extension keys carrying empirica's calibration layer. Kept under an
# ``empirica_`` prefix so they never collide with OKF's own recommended keys.
_EXTENSION_KEYS = ("impact", "confidence", "epistemic_source", "domain", "reversibility", "ai_id", "session_id")


def _iso(ts: Any) -> str | None:
    """Best-effort ISO-8601 timestamp for the OKF ``timestamp`` field."""
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts).isoformat()
        except (ValueError, OSError):
            return None
    if isinstance(ts, str):
        return ts
    return None


def _first(data: dict, keys: tuple[str, ...]) -> str:
    """First non-empty value among keys (checking a nested ``*_data`` block too)."""
    for k in keys:
        v = data.get(k)
        if v:
            return str(v)
    # goals nest the objective under goal_data; findings under finding_data.
    for nested in ("goal_data", "finding_data"):
        block = data.get(nested)
        if isinstance(block, dict):
            for k in keys:
                v = block.get(k)
                if v:
                    return str(v)
    return ""


def _one_sentence(text: str, limit: int = 200) -> str:
    """A single-sentence summary for the OKF ``description`` field."""
    text = " ".join(str(text).split())
    if not text:
        return ""
    cut = text[:limit]
    dot = cut.find(". ")
    if dot > 0:
        return cut[: dot + 1]
    return cut + ("…" if len(text) > limit else "")


def build_concept(spec: dict[str, Any], artifact_id: str, data: dict) -> dict[str, Any] | None:
    """Project one empirica artifact into an OKF concept dict, or None if it has
    no headline text (nothing worth a concept file).

    Returns ``{concept_id, filename, frontmatter, body, links}`` where ``links``
    is a list of ``(label, target_concept_id)`` derived from the artifact's
    parent + source references (OKF conveys relationships as markdown links)."""
    title = _first(data, spec["title_keys"])
    if not title:
        return None

    ns = spec["ns"]
    concept_id = f"{ns}/{artifact_id}"

    frontmatter: dict[str, Any] = {"type": spec["type"], "title": _one_sentence(title, 120)}
    desc_source = _first(data, spec["body_keys"]) or title
    description = _one_sentence(desc_source)
    if description:
        frontmatter["description"] = description
    frontmatter["tags"] = ["empirica", ns]
    ts = _iso(data.get("created_at") or data.get("created_timestamp") or data.get("timestamp"))
    if ts:
        frontmatter["timestamp"] = ts
    frontmatter["empirica_id"] = artifact_id
    for k in _EXTENSION_KEYS:
        v = data.get(k)
        if v is not None and v != "":
            frontmatter[f"empirica_{k}"] = v

    # Body: the full title text + each populated body field as its own section.
    body_lines = [title.strip(), ""]
    labels = {
        "why_failed": "Why it failed",
        "why_wrong": "Why it was wrong",
        "prevention": "Prevention",
        "rationale": "Rationale",
        "resolved_by": "Resolved by",
        "description": "Detail",
    }
    for k in spec["body_keys"]:
        v = data.get(k)
        if v:
            body_lines += [f"## {labels.get(k, k.replace('_', ' ').title())}", "", str(v).strip(), ""]

    # Links (OKF = markdown links, untyped edges). Parent artifact + sources.
    links: list[tuple[str, str]] = []
    parent_id = data.get("parent_id")
    parent_type = data.get("parent_type")
    if parent_id and parent_type:
        links.append((f"parent {parent_type}", f"{parent_type}s/{parent_id}"))
    for ref in data.get("source_refs") or data.get("sourced_from") or []:
        if isinstance(ref, str):
            links.append(("source", f"sources/{ref}"))

    return {
        "concept_id": concept_id,
        "filename": f"{concept_id}.md",
        "frontmatter": frontmatter,
        "body": "\n".join(body_lines).rstrip() + "\n",
        "links": links,
    }


def render_concept(concept: dict[str, Any]) -> str:
    """Render a concept dict as an OKF markdown file (frontmatter + body + links)."""
    fm = yaml.safe_dump(concept["frontmatter"], default_flow_style=False, sort_keys=False, allow_unicode=True)
    parts = ["---", fm.rstrip(), "---", "", concept["body"].rstrip(), ""]
    links = concept.get("links") or []
    if links:
        parts += ["## Related", ""]
        for label, target in links:
            parts.append(f"- {label}: [{target}](/{target}.md)")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def render_index(concepts: list[dict[str, Any]]) -> str:
    """The OKF-reserved ``index.md`` — a by-type directory listing of concepts."""
    lines = [
        "---",
        "type: Index",
        "title: Empirica Epistemic Graph",
        f"description: {len(concepts)} concepts exported from an Empirica project's epistemic graph.",
        "---",
        "",
        "# Empirica Epistemic Graph (OKF bundle)",
        "",
        "Exported by [Empirica](https://getempirica.com). Each concept is a markdown",
        "file with YAML frontmatter; `empirica_*` keys carry the calibration layer",
        "(impact, confidence, provenance) that plain OKF omits.",
        "",
    ]
    by_type: dict[str, list[dict[str, Any]]] = {}
    for c in concepts:
        by_type.setdefault(c["frontmatter"]["type"], []).append(c)
    for ctype in sorted(by_type):
        items = by_type[ctype]
        lines += [f"## {ctype} ({len(items)})", ""]
        for c in sorted(items, key=lambda x: x["frontmatter"]["title"].lower()):
            lines.append(f"- [{c['frontmatter']['title']}](/{c['concept_id']}.md)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate_okf_bundle(
    workspace_root: str | Path, output_dir: str | Path | None = None, verbose: bool = False
) -> dict:
    """Read the project's git-notes artifacts and write an OKF bundle.

    Returns ``{ok, output_dir, concept_count, by_type}``."""
    from empirica.cli.command_handlers.artifacts_commands import _read_all_notes

    workspace = str(workspace_root)
    out = Path(output_dir) if output_dir else Path(workspace) / ".empirica" / "okf"
    out.mkdir(parents=True, exist_ok=True)

    concepts: list[dict[str, Any]] = []
    for spec in ARTIFACT_SPECS:
        for artifact_id, data in _read_all_notes(workspace, spec["ns"]):
            if not isinstance(data, dict):
                continue
            concept = build_concept(spec, artifact_id, data)
            if concept:
                concepts.append(concept)

    for concept in concepts:
        target = out / concept["filename"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render_concept(concept), encoding="utf-8")

    (out / "index.md").write_text(render_index(concepts), encoding="utf-8")

    by_type: dict[str, int] = {}
    for c in concepts:
        by_type[c["frontmatter"]["type"]] = by_type.get(c["frontmatter"]["type"], 0) + 1

    if verbose:
        print(f"OKF bundle: {len(concepts)} concepts → {out}/")
        for t, n in sorted(by_type.items()):
            print(f"  {t}: {n}")

    return {"ok": True, "output_dir": str(out), "concept_count": len(concepts), "by_type": by_type}

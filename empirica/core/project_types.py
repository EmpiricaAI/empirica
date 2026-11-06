"""The one list of project types, and what each type implies (goal 050341d2, David 2026-10-29).

A project has ONE primary type: what it is, which is what defines the rules of its work. Secondary shapes go in the free-form project tags (Empirica
core is a cli first; it is also a library and a service). The per-change classification is a different field, `kind`.

Before this module the vocabulary was defined or defaulted in about a dozen places that disagreed: the repository, ProjectConfig, three parsers,
three DDL defaults of 'product', two type-to-calibration-domain maps (POSTFLIGHT's had only legacy names, so type `software` resolved to the
`default` calibration domain there; project-init's had only the new names), the workspace listing, the bootstrap payload and formatter.

The software shapes come from the software-types 1.0.0 vocabulary (SoftwareUnderstanding) and schema.org where they cover them, plus the two they
lack, browser-extension and infrastructure. The non-code types are the ones project-init already offered.

A type left unset is stored as NULL, "unclassified": inventing `software` for a project nobody classified is what put 21 of 34 registry rows at
`product`. LEGACY_TYPES exists only so rows written before this module stay readable until they are reclassified; it is rejected on input, and
emptying it is the one-line step that removes the legacy values for good.
"""

from __future__ import annotations

import re
from pathlib import Path

SOFTWARE_TYPES: tuple[str, ...] = (
    "web-app",
    "service",
    "cli",
    "library",
    "desktop-app",
    "mobile-app",
    "browser-extension",
    "infrastructure",
)
NON_CODE_TYPES: tuple[str, ...] = (
    "content",
    "research",
    "data",
    "design",
    "operations",
    "strategic",
    "engagement",
    "legal",
)
PROJECT_TYPES: tuple[str, ...] = SOFTWARE_TYPES + NON_CODE_TYPES

#: What each type is, in one line. The `--type` help, the refusal of an unknown value and the table in
#: docs/reference/TAXONOMY.md section 4.6 all read this, so the meanings cannot drift apart.
TYPE_MEANINGS: dict[str, str] = {
    "web-app": "A site or app in the browser",
    "service": "A daemon or HTTP API other things call",
    "cli": "A command-line or terminal tool",
    "library": "Code other code builds on, including a package",
    "desktop-app": "A graphical desktop app",
    "mobile-app": "A phone or tablet app",
    "browser-extension": "An extension for a browser",
    "infrastructure": "Deployment, images and distribution plumbing",
    "content": "Writing, media and published material",
    "research": "Investigation that produces knowledge",
    "data": "Datasets and their pipelines",
    "design": "Visual and product design",
    "operations": "Running systems, scheduling, support",
    "strategic": "Direction, planning and business decisions",
    "engagement": "Work delivered for one client",
    "legal": "Contracts, compliance and legal material",
}


def type_list_text(indent: str = "  ") -> str:
    """The types with their meanings, one per line, aligned. For help text and refusals."""
    width = max(len(name) for name in PROJECT_TYPES)
    return "\n".join(f"{indent}{name.ljust(width)}  {TYPE_MEANINGS[name]}" for name in PROJECT_TYPES)


#: Values rows written before this module may still hold. Readable, never accepted as input. Empty this to remove them.
LEGACY_TYPES: frozenset[str] = frozenset({"software", "product", "application", "feature", "documentation"})

#: Type -> the calibration domain whose category weights apply (confidence_weights.yaml, domain_category_weights).
_CALIBRATION_DOMAIN: dict[str, str] = {
    **dict.fromkeys(SOFTWARE_TYPES, "software"),
    "infrastructure": "operations",
    "content": "consulting",
    "research": "research",
    "data": "research",
    "design": "consulting",
    "operations": "operations",
    "strategic": "consulting",
    "engagement": "consulting",
    "legal": "operations",
}
_LEGACY_CALIBRATION_DOMAIN: dict[str, str] = {
    "software": "software",
    "product": "software",
    "application": "software",
    "feature": "software",
    "documentation": "consulting",
}

#: Type -> (compliance domain, criticality) a transaction starts from when it declares none. Only where the domain's own description matches
#: (empirica/config/domains); security is a flag on a change, never a project default.
_DEFAULT_WORK_FRAME: dict[str, tuple[str, str]] = {
    **dict.fromkeys(SOFTWARE_TYPES, ("default", "medium")),
    "infrastructure": ("default", "high"),
    "research": ("research", "medium"),
    "operations": ("operations", "medium"),
    "engagement": ("consulting", "medium"),
    "strategic": ("consulting", "medium"),
    "content": ("marketing", "medium"),
    "data": ("default", "medium"),
    "design": ("default", "medium"),
    "legal": ("default", "medium"),
}


def accepted_types() -> tuple[str, ...]:
    """The values a row may hold today: the list, then the legacy ones still readable."""
    return PROJECT_TYPES + tuple(sorted(LEGACY_TYPES))


def validate_input(value: str | None) -> str | None:
    """A type a person or caller supplies. None is "unclassified". Unknown and legacy values are refused with the list."""
    if value is None or value == "":
        return None
    if value in PROJECT_TYPES:
        return value
    hint = " (a legacy value: choose the shape that fits the project)" if value in LEGACY_TYPES else ""
    raise ValueError(f"Unknown project type {value!r}{hint}. Valid types:\n{type_list_text()}")


def is_readable(value: str | None) -> bool:
    """Whether a stored value is one this code understands (current list, or legacy while legacy is still readable)."""
    return value is None or value in PROJECT_TYPES or value in LEGACY_TYPES


def calibration_domain(project_type: str | None) -> str:
    """The calibration domain for a stored project type. Unclassified and unknown values use `default`."""
    if not project_type:
        return "default"
    return _CALIBRATION_DOMAIN.get(project_type) or _LEGACY_CALIBRATION_DOMAIN.get(project_type, "default")


def default_work_frame(project_type: str | None) -> tuple[str, str]:
    """(compliance domain, criticality) a transaction of this project starts from. Unclassified and legacy values use ("default", "medium")."""
    return _DEFAULT_WORK_FRAME.get(project_type or "", ("default", "medium"))


_TYPE_LINE = re.compile(r"^type:[ \t]*[\"']?([A-Za-z0-9_-]+)", re.MULTILINE)
_DOMAIN_LINE = re.compile(r"^domain:[ \t]*[\"']?([^\"'#\n]*)", re.MULTILINE)


def project_file_labels(project_path) -> tuple[str | None, str]:
    """(type, domain text) from the top-level lines of a project's .empirica/project.yaml. (None, "") when unset, absent or unreadable.

    A line match, not a YAML parse: PREFLIGHT reads this on the hot path, and parsing the file (it carries calibration weights) cost
    about 5 ms against well under 0.1 ms for the match. The domain is returned as written; whether it names a domain file is the
    caller's question."""
    try:
        # errors="replace": a byte that is not valid UTF-8 elsewhere in the file must not cost the labels, and must never fail a caller
        text = (Path(project_path) / ".empirica" / "project.yaml").read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError, TypeError):
        return None, ""
    type_match = _TYPE_LINE.search(text)
    domain_match = _DOMAIN_LINE.search(text)
    return (type_match.group(1) if type_match else None), (domain_match.group(1).strip() if domain_match else "")


def classification_note(project_path) -> str:
    """One line for a practice whose project file needs a label set, else "". Shown at session start; changes nothing.

    A practice gets its type from the practitioner inhabiting it, not from a central edit (David, 2026-11-06), so the practice has to
    be told: no type, a legacy type, a type this code does not know, or free text where a domain name belongs."""
    if not (Path(project_path) / ".empirica" / "project.yaml").is_file():
        return ""
    project_type, domain = project_file_labels(project_path)
    parts = []
    set_type = "Set it with `empirica project-update --type <type>` (`empirica project-update --help` lists the types)."
    if project_type is None:
        parts.append(f"This project has no project type. {set_type}")
    elif project_type in LEGACY_TYPES:
        parts.append(f"This project's type `{project_type}` is a legacy value. {set_type}")
    elif project_type not in PROJECT_TYPES:
        parts.append(f"This project's type `{project_type}` is not a project type. {set_type}")
    if domain:
        from empirica.config.domain_registry import known_domain_names

        if domain not in known_domain_names(project_path):
            parts.append(
                f"Its domain `{domain}` is free text, not a domain name: the next `empirica project-update` moves it to the tags."
            )
    return " ".join(parts)

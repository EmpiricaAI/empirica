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
    raise ValueError(f"Unknown project type {value!r}{hint}. Valid types: {', '.join(PROJECT_TYPES)}")


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

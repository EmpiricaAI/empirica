"""
A1 Domain Registry — SPEC 1 Part 1 implementation.

Maps (work_type, domain, criticality) tuples to compliance checklists.
Each checklist declares which deterministic checks are required for "done"
at a given criticality level.

Precedence: project .empirica/domains.yaml > user ~/.empirica/domains/*.yaml
> built-in empirica/config/domains/*.yaml.

See: .empirica/visions/2026-04-08-sentinel-reframe-api-contract-spec.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# Criticality levels in order (lowest → highest).
# Used for fallback resolution: if exact level not found, walk down.
CRITICALITY_ORDER = ("critical", "high", "medium", "low")


@dataclass(frozen=True)
class DomainKey:
    """Lookup key for a domain checklist."""

    work_type: str
    domain: str = "default"
    criticality: str = "medium"


@dataclass(frozen=True)
class Checklist:
    """What "done" means for a (work_type, domain, criticality) tuple."""

    required: tuple[str, ...]
    optional: tuple[str, ...]
    thresholds: dict[str, float]
    max_iterations: int
    hints_to_ai: tuple[str, ...] = ()

    @property
    def has_checks(self) -> bool:
        return len(self.required) > 0

    @classmethod
    def empty(cls) -> Checklist:
        return cls(
            required=(),
            optional=(),
            thresholds={},
            max_iterations=1,
            hints_to_ai=(),
        )


@dataclass
class _DomainEntry:
    """Internal: parsed representation of one domain YAML."""

    name: str
    description: str
    applies_to_work_types: list[str]
    criticalities: dict[str, Checklist]
    hints_to_ai: tuple[str, ...] = ()
    source: str = "builtin"  # "builtin" | "user" | "project"


class DomainRegistry:
    """Loads and resolves domain/criticality → checklist mappings.

    Resolution order at lookup:
    1. Exact (work_type, domain, criticality) match
    2. Same work_type + domain, next-lower criticality
    3. Same work_type + "default" domain + original criticality
    4. Built-in baseline (empty checklist)
    """

    def __init__(
        self,
        project_path: Path | None = None,
        user_dir: Path | None = None,
        builtin_dir: Path | None = None,
    ):
        self._domains: dict[str, _DomainEntry] = {}

        if builtin_dir is None:
            builtin_dir = Path(__file__).parent / "domains"
        if user_dir is None:
            user_dir = Path.home() / ".empirica" / "domains"

        # Load in reverse precedence order — later loads override earlier
        self._load_directory(builtin_dir, source="builtin")
        self._load_directory(user_dir, source="user")
        if project_path is not None:
            proj_file = Path(project_path) / ".empirica" / "domains.yaml"
            if proj_file.exists():
                self._load_project_file(proj_file)

    # -----------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------

    def resolve(self, key: DomainKey) -> Checklist:
        """Return the checklist for a (work_type, domain, criticality) tuple.

        Fallback chain:
        1. Exact match on domain + criticality (if work_type allowed)
        2. Same domain, next-lower criticality
        3. "default" domain at original criticality
        4. Empty checklist
        """
        # Try exact domain
        cl = self._try_resolve(key.work_type, key.domain, key.criticality)
        if cl is not None:
            return cl

        # Try "default" domain if the requested one didn't match
        if key.domain != "default":
            cl = self._try_resolve(key.work_type, "default", key.criticality)
            if cl is not None:
                return cl

        return Checklist.empty()

    def list_domains(self) -> list[str]:
        """All loaded domain names."""
        return sorted(self._domains.keys())

    def list_criticalities(self, domain: str) -> list[str]:
        """All criticality levels defined for a domain."""
        entry = self._domains.get(domain)
        if entry is None:
            return []
        return sorted(entry.criticalities.keys())

    def get_domain_entry(self, domain: str) -> _DomainEntry | None:
        """Get full domain entry for display/CLI."""
        return self._domains.get(domain)

    # -----------------------------------------------------------------
    # Resolution internals
    # -----------------------------------------------------------------

    def _try_resolve(self, work_type: str, domain: str, criticality: str) -> Checklist | None:
        entry = self._domains.get(domain)
        if entry is None:
            return None

        # Check work_type filter
        if entry.applies_to_work_types and work_type not in entry.applies_to_work_types:
            return None

        # Try exact criticality
        if criticality in entry.criticalities:
            return entry.criticalities[criticality]

        # Fail closed. A missing level used to walk DOWN to a weaker checklist, so a
        # domain that defined only {high, low} gave a medium task the low one. Look at
        # the nearest STRICTER level first. An unknown criticality (a typo) is treated
        # as the strictest rather than skipped (the old walk started past "critical").
        if criticality in CRITICALITY_ORDER:
            rank = CRITICALITY_ORDER.index(criticality)
            for level in reversed(CRITICALITY_ORDER[:rank]):
                if level in entry.criticalities:
                    return entry.criticalities[level]

        # Nothing at or above the demand: use the strictest level the domain defines,
        # and say so, because the checklist is weaker than what was asked for.
        for level in CRITICALITY_ORDER:
            if level in entry.criticalities:
                logger.warning(
                    "Domain %r defines no %r checklist or stricter; using its strictest, %r",
                    domain,
                    criticality,
                    level,
                )
                return entry.criticalities[level]

        return None

    # -----------------------------------------------------------------
    # YAML loading
    # -----------------------------------------------------------------

    def _load_directory(self, directory: Path, source: str) -> None:
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.yaml")):
            self._load_domain_file(path, source)

    def _load_domain_file(self, path: Path, source: str) -> None:
        try:
            raw = yaml.safe_load(path.read_text())
        except Exception as e:
            logger.warning("Skipping malformed domain file %s: %s", path, e)
            return

        if not isinstance(raw, dict):
            logger.warning("Skipping %s: not a dict", path)
            return

        domain_name = raw.get("domain")
        if not domain_name:
            logger.warning("Skipping %s: missing 'domain' key", path)
            return

        entry = self._parse_domain(raw, source)
        if entry is not None:
            # Later source (higher precedence) overwrites earlier
            self._domains[entry.name] = entry

    def _load_project_file(self, path: Path) -> None:
        try:
            raw = yaml.safe_load(path.read_text())
        except Exception as e:
            logger.warning("Skipping project domains file %s: %s", path, e)
            return

        if not isinstance(raw, dict):
            return

        # Aggregator format: {"version": "1", "domains": {"name": {...}, ...}}
        domains_dict = raw.get("domains", {})
        if isinstance(domains_dict, dict):
            for name, domain_data in domains_dict.items():
                if isinstance(domain_data, dict):
                    # Ensure domain name is set
                    if "domain" not in domain_data:
                        domain_data["domain"] = name
                    entry = self._parse_domain(domain_data, "project")
                    if entry is not None:
                        self._domains[entry.name] = entry

    def _parse_domain(self, raw: dict[str, Any], source: str) -> _DomainEntry | None:
        domain_name = raw.get("domain", "")
        if not domain_name:
            return None

        crits_raw = raw.get("criticalities", {})
        if not isinstance(crits_raw, dict):
            return None

        criticalities: dict[str, Checklist] = {}
        for level, level_data in crits_raw.items():
            if not isinstance(level_data, dict):
                continue
            criticalities[level] = Checklist(
                required=tuple(level_data.get("required_checks", [])),
                optional=tuple(level_data.get("optional_checks", [])),
                thresholds=level_data.get("thresholds", {}),
                max_iterations=level_data.get("max_iterations", 5),
                hints_to_ai=tuple(level_data.get("hints_to_ai", [])),
            )

        return _DomainEntry(
            name=domain_name,
            description=raw.get("description", ""),
            applies_to_work_types=raw.get("applies_to_work_types", []),
            criticalities=criticalities,
            hints_to_ai=tuple(raw.get("hints_to_ai", [])),
            source=source,
        )

"""
Project Configuration Loader

Loads .empirica/project.yaml configuration including subject definitions
and path mappings for context filtering.
"""

import logging
from pathlib import Path
from typing import Any, ClassVar

import yaml

logger = logging.getLogger(__name__)


class ProjectConfig:
    """Project configuration with subject mappings and v2.0 identity metadata."""

    # Valid project types (universal across domains)
    VALID_TYPES: ClassVar[list[str]] = [
        "software",
        "content",
        "research",
        "data",
        "design",
        "operations",
        "strategic",
        "engagement",
        "legal",
        # Legacy types (backward compat with v1.x project.yaml files)
        "product",
        "application",
        "feature",
        "documentation",
        "infrastructure",
    ]

    VALID_CLASSIFICATIONS: ClassVar[list[str]] = ["open", "internal", "restricted"]

    VALID_STATUSES: ClassVar[list[str]] = ["active", "dormant", "archived"]

    def __init__(self, config_data: dict[str, Any]) -> None:
        """Initialize project config from configuration dictionary."""
        # Schema version
        self.version = config_data.get("version", "1.0")

        # Core identity (v1.0)
        self.project_id = config_data.get("project_id")
        self.name = config_data.get("name", "Unknown Project")
        self.description = config_data.get("description", "")

        # Identity enrichment (v2.0)
        self.type = self._validated(config_data.get("type", "software"), self.VALID_TYPES, "software", "type")
        self.domain = config_data.get("domain", "")
        self.classification = self._validated(
            config_data.get("classification", "internal"), self.VALID_CLASSIFICATIONS, "internal", "classification"
        )
        self.status = self._validated(config_data.get("status", "active"), self.VALID_STATUSES, "active", "status")

        # Evidence & language
        self.evidence_profile = config_data.get("evidence_profile", "auto")
        self.languages = config_data.get("languages", [])
        self.tags = config_data.get("tags", [])

        # Provenance
        self.created_at = config_data.get("created_at")
        self.created_by = config_data.get("created_by")
        self.repository = config_data.get("repository")

        # Participant references (IDs + roles — full profiles in Workspace CRM)
        self.contacts = config_data.get("contacts", [])

        # Engagement references (IDs — lifecycle in Workspace CRM)
        self.engagements = config_data.get("engagements", [])

        # Relationship edges (typed links to other entities)
        self.edges = config_data.get("edges", [])

        # Existing v1.0 fields
        self.subjects = config_data.get("subjects", {})
        self.default_subject = config_data.get("default_subject")
        self.auto_detect = config_data.get("auto_detect", {"enabled": True, "method": "path_match"})
        self.beads = config_data.get("beads", {})
        self.default_use_beads = self.beads.get("default_enabled", False)

        # Domain extension point
        self.domain_config = config_data.get("domain_config", {})

    @staticmethod
    def _validated(value: str, valid_set: list[str], default: str, field_name: str) -> str:
        """Validate value against known set, warn and default if invalid."""
        if value in valid_set:
            return value
        logger.warning(f"Unknown {field_name} '{value}', defaulting to '{default}'")
        return default

    def to_dict(self) -> dict[str, Any]:
        """Serialize config back to dict for yaml round-tripping."""
        d: dict[str, Any] = {
            "version": self.version,
            "name": self.name,
            "description": self.description,
        }
        if self.project_id:
            d["project_id"] = self.project_id
        d["type"] = self.type
        if self.domain:
            d["domain"] = self.domain
        d["classification"] = self.classification
        d["status"] = self.status
        d["evidence_profile"] = self.evidence_profile
        if self.languages:
            d["languages"] = self.languages
        if self.tags:
            d["tags"] = self.tags
        if self.created_at:
            d["created_at"] = self.created_at
        if self.created_by:
            d["created_by"] = self.created_by
        if self.repository:
            d["repository"] = self.repository
        if self.contacts:
            d["contacts"] = self.contacts
        if self.engagements:
            d["engagements"] = self.engagements
        if self.edges:
            d["edges"] = self.edges
        d["beads"] = self.beads if self.beads else {"default_enabled": False}
        if self.subjects:
            d["subjects"] = self.subjects
        d["auto_detect"] = self.auto_detect
        if self.domain_config:
            d["domain_config"] = self.domain_config
        return d

    def get_subject_for_path(self, current_path: str) -> str | None:
        """
        Detect subject from current working directory.

        Args:
            current_path: Current working directory

        Returns:
            subject_id if matched, None otherwise
        """
        if not self.auto_detect.get("enabled", True):
            return None

        resolved_path = Path(current_path).resolve()

        # Try to match current path to subject paths
        for subject_id, subject_config in self.subjects.items():
            for path_pattern in subject_config.get("paths", []):
                # Convert to absolute path
                subject_path = Path(path_pattern).resolve()

                # Check if current path is within subject path
                try:
                    resolved_path.relative_to(subject_path)
                    logger.info(f"Auto-detected subject: {subject_id} (matched {path_pattern})")
                    return subject_id
                except ValueError:
                    # Not a subpath, continue
                    continue

        logger.debug(f"No subject auto-detected for path: {resolved_path}")
        return None

    def get_subject_info(self, subject_id: str) -> dict[str, Any] | None:
        """Get subject configuration"""
        return self.subjects.get(subject_id)

    def list_subjects(self) -> list[str]:
        """List all subject IDs"""
        return list(self.subjects.keys())


def load_project_config(project_root: Path | None = None) -> ProjectConfig | None:
    """
    Load project configuration from .empirica/project.yaml

    Note: project_id is taken from sessions.db (authoritative) if available,
    with project.yaml as fallback for fresh projects. Other config data
    (name, subjects, etc.) always comes from project.yaml.

    Args:
        project_root: Root directory of project (defaults to current directory)

    Returns:
        ProjectConfig if found, None otherwise
    """
    if project_root is None:
        project_root = Path.cwd()

    config_path = project_root / ".empirica" / "project.yaml"

    if not config_path.exists():
        logger.debug(f"No project config found at {config_path}")
        return None

    try:
        with open(config_path) as f:
            config_data = yaml.safe_load(f)

        # Override project_id with authoritative value from sessions.db
        # This prevents UUID mismatch when yaml and workspace.db diverge
        try:
            from empirica.utils.session_resolver import InstanceResolver as R

            db_project_id = R.project_id_from_db(project_root)
            if db_project_id:
                config_data["project_id"] = db_project_id
        except Exception:  # noqa: S110 — keep yaml project_id as fallback if DB unavailable
            pass

        logger.info(f"Loaded project config: {config_data.get('name', 'Unknown')}")
        return ProjectConfig(config_data)

    except Exception as e:
        logger.error(f"Failed to load project config from {config_path}: {e}")
        return None


def get_current_subject(project_config: ProjectConfig | None = None, current_path: Path | None = None) -> str | None:
    """
    Get current subject based on working directory.

    Args:
        project_config: Project configuration (loads if None)
        current_path: Current working directory (uses cwd if None)

    Returns:
        subject_id if detected, None otherwise
    """
    if project_config is None:
        project_config = load_project_config()

    if project_config is None:
        return None

    if current_path is None:
        current_path = Path.cwd()

    return project_config.get_subject_for_path(str(current_path))


def compose_ai_id_forms(
    *,
    tenant_slug: str,
    mesh_id_prefix: str,
    basename: str,
) -> dict[str, str]:
    """Compose the three ai_id forms for cross-AI mesh addressing.

    Mirrors cortex.tenant.compose_ai_id_forms — kept local so empirica doesn't
    take a runtime dependency on the cortex package. Trusts `mesh_id_prefix`
    as cortex returns it (via /v1/users/me or cortex_session_init) rather
    than recomputing it, so we don't drift if cortex changes its slug rule.

    Forms (progressively qualified — pick the narrowest that disambiguates):
      - short:  `<basename>`                        — within-project default
      - tenant: `<tenant_slug>_<basename>`          — within-tenant
      - mesh:   `<mesh_id_prefix>_<basename>`       — cross-tenant / fully qualified
    """
    return {
        "short": basename,
        "tenant": f"{tenant_slug}_{basename}",
        "mesh": f"{mesh_id_prefix}_{basename}",
    }


def compose_canonical_seat(*, mesh_id_prefix: str, ai_id: str) -> str | None:
    """Compose the strict canonical 3-form mesh seat `org.tenant.project`.

    This is the DOT-separated wire form that cortex's strict-canonical
    resolver (`find_users_for_ai_id`, which decodes via `split('.', 2)`)
    and the `seat` parameter of `cortex_session_init` expect. It is
    DISTINCT from `compose_ai_id_forms` above, whose `_`-joined forms are
    the older mesh-addressing convention — do not use those for `seat`.

    `mesh_id_prefix` is the `<org>.<tenant>` prefix cortex returns in
    session_init / `/v1/users/me` (e.g. `empirica.david`); `ai_id` is the
    project's canonical basename (e.g. `empirica`). Result:
    `empirica.david.empirica`.

    Returns None when either input is empty, so callers can skip writing a
    malformed seat (a wrong-form seat is rejected silently by the
    strict-canonical send resolver).
    """
    basename = (ai_id or "").strip()
    if not basename:
        return None
    # Already a full canonical 3-form? Some practices store their ai_id
    # already-qualified (e.g. `empirica-foundation.carly.flta`); prepending the
    # prefix again would DOUBLE-prefix it. Mirror
    # content_poll._resolve_canonical_ai_id: a dot means already-canonical, so
    # pass it through unchanged (prop_mzsijy2).
    if "." in basename:
        return basename
    prefix = (mesh_id_prefix or "").strip().rstrip(".")
    if not prefix:
        return None
    return f"{prefix}.{basename}"

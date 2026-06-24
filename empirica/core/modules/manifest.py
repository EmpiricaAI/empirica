"""``module.yaml`` — the practice-module manifest schema, loader, and validator.

A practice-module declares its install bill-of-materials in a ``module.yaml``
under a top-level ``empirica_module:`` key. The MIT core reads it *declaratively*
(pydantic models, no import of any module's code) to understand what the
manifest would install across the two install layers:

- **seat layer** → autonomy's ``install_seat.py`` (CLAUDE.md managed-block).
  ``seat.import`` / ``seat.mode`` / top-level ``seat_name`` map onto the
  ``--seat-import`` / ``--mode`` / ``--seat-name`` flags.
- **plugin layer** → ``empirica module provision`` (skills/agents/automations,
  ntfy topics, env presence checks).

Distribution artifacts (``artifacts``) are fetched by ``empirica module fetch``
as a pre-step before either layer runs (install_seat itself never fetches).

Validation is structural and fail-fast: a malformed manifest is rejected with a
precise error *before* any install action runs. ``extra="forbid"`` turns a
mis-spelled key into a loud error rather than a silently-ignored field, and the
``secrets_ref`` validator enforces the reference-only discipline (a raw key is
rejected at the schema layer, not just by the downstream secrets-manager).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

# A secrets reference is a manager pointer, never a raw key: ``doppler://...``,
# ``op://...``, ``vault://...`` (scheme://...) or ``env:VARNAME``. Anything that
# does not match is treated as a raw secret and rejected.
_SECRETS_REF_RE = re.compile(r"^(?:[a-z][a-z0-9+.\-]*://.+|env:[A-Za-z_][A-Za-z0-9_]*)$")

_ROOT_KEY = "empirica_module"


class ManifestError(Exception):
    """Raised when a ``module.yaml`` is missing, unreadable, or invalid."""


class Seat(BaseModel):
    """Seat layer declaration → ``install_seat.py`` flags."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    import_: str = Field(
        alias="import",
        description="@import body doc (relative to the seat root) → install_seat --seat-import",
    )
    mode: Literal["inject", "dedicated"] = "inject"


class Artifacts(BaseModel):
    """Distribution artifacts → ``empirica module fetch`` (auth-gated pre-step)."""

    model_config = ConfigDict(extra="forbid")

    plugin_archive: str | None = Field(
        default=None, description="Plugin archive name fetched from the auth-gated registry"
    )
    python_packages: list[str] = Field(
        default_factory=list, description="Closed-source wheels from an auth-gated index"
    )


class Automation(BaseModel):
    """A declared automation wired via ``empirica loop register`` (canonical catalog).

    ``kind=listener`` → a long-running systemd-user *service* (``autostart`` +
    ``restart_policy`` apply). ``kind=interval`` / ``kind=cron`` → a systemd-user
    *timer*.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["listener", "interval", "cron"]
    command: str | None = None
    interval: str | None = None
    cron: str | None = None
    autostart: bool = False
    restart_policy: Literal["no", "on-failure", "always"] = "no"

    @model_validator(mode="after")
    def _kind_requires_field(self) -> Automation:
        if self.kind == "listener" and not self.command:
            raise ValueError(f"automation {self.name!r}: kind=listener requires 'command'")
        if self.kind == "interval" and not self.interval:
            raise ValueError(f"automation {self.name!r}: kind=interval requires 'interval'")
        if self.kind == "cron" and not self.cron:
            raise ValueError(f"automation {self.name!r}: kind=cron requires 'cron'")
        return self


class Provides(BaseModel):
    """Plugin-layer payload → ``empirica module provision``."""

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    automations: list[Automation] = Field(default_factory=list)
    domains: list[str] = Field(
        default_factory=list,
        description="Engagement domain ids this module's practice joins (→ practice_domains at provision)",
    )


class RequiresRuntime(BaseModel):
    """Runtime requirements — presence-validated at install, never raw-held.

    ``env`` names are presence-checked only (the value is never read into the
    provisioner). ``topics`` are registered via the cortex admin ntfy ACL.
    ``secrets_ref`` is a single manager reference both install layers resolve.
    """

    model_config = ConfigDict(extra="forbid")

    env: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    secrets_ref: str | None = None

    @field_validator("secrets_ref")
    @classmethod
    def _ref_only(cls, v: str | None) -> str | None:
        if v is not None and not _SECRETS_REF_RE.match(v):
            raise ValueError(
                "secrets_ref must be a manager REFERENCE (e.g. doppler://, op://, "
                "vault://, env:VARNAME), never a raw key"
            )
        return v


class Requires(BaseModel):
    """Compatibility constraints checked before install."""

    model_config = ConfigDict(extra="forbid")

    empirica_core: str | None = None
    cortex_api: str | None = None


class ModuleManifest(BaseModel):
    """The ``empirica_module:`` block of a ``module.yaml``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Plugin/module id (drops into ~/.claude/plugins/local/<name>/)")
    seat_name: str = Field(description="Canonical seat id → install_seat --seat-name")
    version: str
    visibility: Literal["public", "private", "enterprise"] = "private"
    requires: Requires = Field(default_factory=Requires)
    seat: Seat
    artifacts: Artifacts = Field(default_factory=Artifacts)
    provides: Provides = Field(default_factory=Provides)
    requires_runtime: RequiresRuntime = Field(default_factory=RequiresRuntime)


def _format_errors(exc: ValidationError) -> list[str]:
    """Render pydantic errors as flat, human-readable ``path: message`` strings."""
    out: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "<root>"
        out.append(f"{loc}: {err['msg']}")
    return out


def load_manifest(path: str | Path) -> ModuleManifest:
    """Load + validate a ``module.yaml``. Raises ``ManifestError`` on any problem.

    The file must contain a top-level ``empirica_module:`` mapping.
    """
    p = Path(path)
    if not p.exists():
        raise ManifestError(f"manifest not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text())
    except yaml.YAMLError as e:
        raise ManifestError(f"invalid YAML in {p}: {e}") from e
    if not isinstance(raw, dict) or _ROOT_KEY not in raw:
        raise ManifestError(f"{p}: missing top-level '{_ROOT_KEY}:' key")
    body = raw[_ROOT_KEY]
    if not isinstance(body, dict):
        raise ManifestError(f"{p}: '{_ROOT_KEY}' must be a mapping")
    try:
        return ModuleManifest.model_validate(body)
    except ValidationError as e:
        raise ManifestError("; ".join(_format_errors(e))) from e


def validate_manifest_file(path: str | Path) -> dict:
    """Validate a ``module.yaml`` and return a CLI-friendly receipt.

    Returns ``{ok, path, errors, manifest}`` — ``manifest`` is the normalized
    dict on success, ``errors`` the list of ``path: message`` strings on failure.
    Never raises for a validation problem (the receipt carries it).
    """
    p = Path(path)
    try:
        manifest = load_manifest(p)
    except ManifestError as e:
        return {"ok": False, "path": str(p), "errors": [str(e)], "manifest": None}
    return {
        "ok": True,
        "path": str(p),
        "errors": [],
        "manifest": manifest.model_dump(by_alias=True),
    }

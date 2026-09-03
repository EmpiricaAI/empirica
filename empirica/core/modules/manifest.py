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
    """What a module contributes — plugin payload AND hosted surfaces.

    The first four are plugin-layer, installed by ``empirica module provision``.
    ``mcp`` and ``services`` are NOT installed by anything here: they name
    capabilities a practice HOSTS, so peers can resolve what they depend on.

    Why they exist: ``requires_runtime.mcp`` was added without a matching
    provides axis, which made the dependency declarable in one direction only.
    Every seat on the fleet requires the cortex MCP server and nothing could
    declare providing it, so a requires→provides drift check could never
    resolve — the union of `requires` across a seat would always contain an
    entry with no possible supplier.

    Raised by empirica-cortex, whose capability surface is exactly a hosted MCP
    server plus a REST API. They left `provides: {}` and flagged it rather than
    shoehorning a hosted service into `skills:`, which was the right call: an
    accurate-and-useless empty is recoverable, a plausible-looking lie is not.
    """

    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    agents: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    automations: list[Automation] = Field(default_factory=list)
    mcp: list[str] = Field(
        default_factory=list,
        description="MCP server names this practice HOSTS (the supply side of requires_runtime.mcp)",
    )
    services: list[str] = Field(
        default_factory=list,
        description="Hosted non-MCP surfaces — a REST API, a webhook sink, a queue. Named, not addressed: "
        "endpoints belong in config, this is for dependency resolution",
    )
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
    # MCP servers the module needs at runtime. Sits beside env/topics because it
    # IS a runtime dependency — a module whose skills call an MCP tool is as
    # broken without that server as without its env vars. Presence-declared
    # only; wiring the server is the harness's job, not the provisioner's.
    mcp: list[str] = Field(default_factory=list)
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


class Declined(BaseModel):
    """Layers this practice DELIBERATELY does not consume, each with its reason.

    The third state. Without it a declaration gate sees two: declared-consumed, and
    nothing — and *nothing* is where a considered refusal and a practice that simply
    forgot become the same bytes. Every manifest in the fleet already carried its
    refusals as YAML comments, which is a note to the next human and invisible to
    every reader.

    **The reason is REQUIRED and must be non-empty**, because a bare list of declined
    names reproduces the original silence one level up: you would know a layer was
    refused and not why, so the gate could report the fact and nothing actionable. The
    reason is the entire value of the third state.

    Both practices that hit this independently invented a `declined:` mapping within
    an hour of each other, and both wrote it in a position `extra="forbid"` rejects.
    Independent reinvention of a shape the schema lacks is the signal; this is that
    shape, hoisted to where it validates.
    """

    model_config = ConfigDict(extra="forbid")

    prompts: dict[str, str] = Field(default_factory=dict)
    skills: dict[str, str] = Field(default_factory=dict)

    @field_validator("prompts", "skills")
    @classmethod
    def _reason_required(cls, v: dict[str, str]) -> dict[str, str]:
        blank = sorted(name for name, reason in v.items() if not (reason or "").strip())
        if blank:
            raise ValueError(
                f"declined entries need a non-empty reason: {', '.join(blank)} — "
                "a declined name without a reason says no more than the silence it replaces"
            )
        return v


class Requires(BaseModel):
    """What this practice CONSUMES, and what it deliberately does not.

    ``skills`` / ``prompts`` name the layers this seat needs present — every manifest
    in the fleet writes them as "what this practice consumes", so that is what they
    mean here. ``declined`` names what it refuses, and why.
    """

    model_config = ConfigDict(extra="forbid")

    empirica_core: str | None = None
    cortex_api: str | None = None
    # Skills and prompt layers this module depends on being present. Declared
    # rather than fetched: the point is a precise pre-install error naming what
    # is missing, instead of a module that installs cleanly and then misbehaves
    # because a skill it assumed was never there.
    skills: list[str] = Field(default_factory=list)
    prompts: list[str] = Field(default_factory=list)
    declined: Declined = Field(default_factory=Declined)

    @model_validator(mode="after")
    def _no_layer_both_consumed_and_declined(self) -> Requires:
        for kind in ("prompts", "skills"):
            both = sorted(set(getattr(self, kind)) & set(getattr(self.declined, kind)))
            if both:
                raise ValueError(
                    f"declined.{kind} contradicts {kind}: {', '.join(both)} is listed as both consumed and declined"
                )
        return self


#: The three states a declaration gate must be able to tell apart.
CONSUMES = "consumes"
DECLINED = "declined"
UNDECLARED = "undeclared"


def declaration_state(manifest: ModuleManifest, layer: str, kind: str = "prompts") -> tuple[str, str | None]:
    """How this manifest declares `layer` — ``(state, reason)``.

    THE READER. A field with no reader is what let this drift persist unnoticed:
    nothing ever compared declared intent to reality, so nobody could see that the
    intent had nowhere to live. Shipping the shape without the reader would repeat
    that exactly.

    Consumers gating on declarations (ecosystem-update's report, provisioners, audits)
    should ask through here rather than reading the lists themselves, so every gate in
    the fleet asks the same question the same way. ``reason`` is non-None only for
    ``DECLINED``.
    """
    if kind not in ("prompts", "skills"):
        raise ValueError(f"kind must be 'prompts' or 'skills', got {kind!r}")
    if layer in getattr(manifest.requires, kind):
        return CONSUMES, None
    declined = getattr(manifest.requires.declined, kind)
    if layer in declined:
        return DECLINED, declined[layer]
    return UNDECLARED, None


class ModuleManifest(BaseModel):
    """The ``empirica_module:`` block of a ``module.yaml``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Plugin/module id (drops into ~/.claude/plugins/local/<name>/)")
    seat_name: str = Field(description="Canonical seat id → install_seat --seat-name")
    version: str
    visibility: Literal["public", "private", "enterprise"] = "private"
    requires: Requires = Field(default_factory=Requires)
    # OPTIONAL — "practice with a manifest" and "practice with a seat" are
    # different sets, and cortex is the proof: its role body ships through
    # ecosystem-update's prompts component rather than the seat mechanism, so a
    # `seat.import` in its repo would either point at a file it does not hold or
    # duplicate content homed elsewhere. Requiring the block forced a choice
    # between fabricating a seat doc to satisfy a validator and not having a
    # manifest at all.
    #
    # Note this is the BLOCK, not the identity: `seat_name` stays required,
    # because every practice has a canonical id (executors.py keys
    # join_practice_domain off it) even when it has no seat LAYER to install.
    # Nothing in core dereferences seat.import_/seat.mode — the block is
    # declarative, read by autonomy's install_seat.py from the file itself.
    seat: Seat | None = None
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
        # Surfaced in the receipt so `module-validate` shows the three states rather
        # than only whether the file parses. A declared refusal that no surface ever
        # renders is a comment with extra steps.
        "declarations": {
            kind: {
                CONSUMES: sorted(getattr(manifest.requires, kind)),
                DECLINED: dict(sorted(getattr(manifest.requires.declined, kind).items())),
            }
            for kind in ("prompts", "skills")
        },
    }

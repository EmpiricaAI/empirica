"""MeshContent — common substrate for cross-practice primitives.

Per `docs/architecture/MESH_CONTENT.md`. This module is the empirica-side
home for the helpers + contract that every mesh-citizen primitive
(Source today, SER and future types tomorrow) implements:

1. **Canonical address** — `<org>.<tenant>.<practice>~<type>_<uuid>`.
   Separator `~` chosen per cortex's pushback on `#` (HTTP fragment
   semantics get dropped by browser caches / ntfy / some HTTP clients
   — fragile in transport). Final format on prop_w7q24hdurnhfnasf2ahiq5gvte
   thread; `~` is the current convergence between empirica + cortex.
2. **Visibility tier** — re-exported from `empirica.data.visibility`
   so substrate consumers have one import surface.
3. **Render pipeline** — `RenderResult` dataclass for the canonical
   bytes + hash + size cap that mesh-promotion calls expect.

This is Slice 1 (thin first slice from §7 of the design doc):
neutral on the addressing question, gives consumers the import surface,
ships immediately so the canonical-address thread can land without
blocking other work.

Storage stays primitive-specific. Source rows stay in
`epistemic_sources`; SER records stay in cortex storage. This module
is contract + helpers, not data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from empirica.data.visibility import (
    DEFAULT_VISIBILITY,
    VISIBILITY_TIERS,
    normalize_visibility,
)

__all__ = [
    "CANONICAL_SEPARATOR",
    "DEFAULT_RENDER_SIZE_CAP",
    "DEFAULT_VISIBILITY",
    "MESH_CONTENT_TYPES",
    "VISIBILITY_TIERS",
    "RenderResult",
    "canonical_address",
    "compute_sha256",
    "idempotency_key",
    "normalize_visibility",
    "parse_canonical_address",
    "truncate_to_cap",
]


# Mesh-content type registry — short token used in canonical addresses.
# Add new entries here when a new primitive joins the substrate.
MESH_CONTENT_TYPES: tuple[str, ...] = (
    "src",  # Source (empirica-side, epistemic_sources)
    "ser",  # Shared Epistemic Record (cortex-side)
    # Future: "dec" (shared decision), "les" (shared lesson), ...
)


# Render size cap matches the daemon `GET /api/v1/sources/{id}/content`
# endpoint (shipped earlier). Per `prop_3u54utxe` discussion, the same
# cap covers SER reports too (they're typically much smaller).
DEFAULT_RENDER_SIZE_CAP = 10 * 1024 * 1024  # 10 MB


# ── Canonical address ─────────────────────────────────────────────────


CANONICAL_SEPARATOR = "~"
"""Separator between the practice id and the type-prefixed uuid.

`~` chosen over `#`/`::`/`@`/underscore per cortex's transport-safety
analysis (prop_kxpvlgc65n on the canonical-source thread):

  - `#` is fragile — HTTP fragment semantics get dropped by clients,
    browser caches, ntfy payloads
  - `::` collides with mesh-wire formats
  - `@` carries email-syntax ambiguity (low-risk but present)
  - underscore is parseable but ambiguous against slug parts
  - `~` is transport-clean, reads cleanly, no semantic baggage

Until the wider design thread locks an even-more-canonical answer,
`~` is the empirica+cortex convergence.
"""


def canonical_address(practice: str, content_type: str, uuid_str: str) -> str:
    """Derive the canonical mesh address for a piece of MeshContent.

    Format: ``<practice><~><type>_<uuid>``

    Where ``<practice>`` is the 3-level canonical practice id
    (``<org>.<tenant>.<project>``, e.g. ``empirica.david.empirica``).

    Example: ``empirica.david.empirica-extension~src_abc123``

    Raises:
        ValueError: if ``content_type`` is not in ``MESH_CONTENT_TYPES``,
            or if any argument is empty.
    """
    if not practice:
        raise ValueError("practice required (3-level canonical id)")
    if not uuid_str:
        raise ValueError("uuid required")
    if content_type not in MESH_CONTENT_TYPES:
        raise ValueError(f"unknown content_type {content_type!r}; expected one of {MESH_CONTENT_TYPES}")
    return f"{practice}{CANONICAL_SEPARATOR}{content_type}_{uuid_str}"


def parse_canonical_address(addr: str) -> tuple[str, str, str]:
    """Reverse of `canonical_address`. Returns (practice, content_type, uuid).

    Raises:
        ValueError: if the address doesn't match the expected shape.
    """
    if not addr or CANONICAL_SEPARATOR not in addr:
        raise ValueError(f"not a canonical address: {addr!r}")
    practice, _, rest = addr.partition(CANONICAL_SEPARATOR)
    if not practice or not rest or "_" not in rest:
        raise ValueError(f"malformed canonical address: {addr!r}")
    content_type, _, uuid_str = rest.partition("_")
    if content_type not in MESH_CONTENT_TYPES:
        raise ValueError(f"unknown content_type {content_type!r} in {addr!r}; expected one of {MESH_CONTENT_TYPES}")
    if not uuid_str:
        raise ValueError(f"missing uuid in canonical address: {addr!r}")
    return practice, content_type, uuid_str


# ── Render pipeline ───────────────────────────────────────────────────


@dataclass
class RenderResult:
    """Canonical bytes ready for mesh promotion.

    Returned by every MeshContent primitive's ``render()`` implementation.
    Cortex-side promotion accepts this shape directly:

      - ``canonical_bytes`` — the payload itself
      - ``content_type`` — MIME, used for browser-side rendering
      - ``size_bytes`` — pre-truncation size, for cap reporting
      - ``sha256`` — hex digest of ``canonical_bytes``, for dedup
      - ``truncated`` — True iff the size cap was applied
    """

    canonical_bytes: bytes
    content_type: str = "text/markdown; charset=utf-8"
    size_bytes: int = 0
    sha256: str = ""
    truncated: bool = False
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.size_bytes:
            self.size_bytes = len(self.canonical_bytes)
        if not self.sha256:
            self.sha256 = compute_sha256(self.canonical_bytes)


def compute_sha256(data: bytes) -> str:
    """Hex sha256 of the bytes. Used for cortex-side content dedup."""
    return hashlib.sha256(data).hexdigest()


# ── Idempotency key (W1a — idempotent-by-design) ──────────────────────

# Param keys that never define an action's *identity* — dropped before
# hashing so two proposals of the same action collide regardless of
# incidental differences (phrasing, emission time, per-emission ids).
# Top-level only, by design: the semantic params that identify an action
# live at the top level; nested structures are hashed as-is (sorted).
_VOLATILE_PARAM_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "ts",
        "created_at",
        "updated_at",
        "nonce",
        "proposal_id",
        "parent_id",
        "thread_root_id",
        "summary",
        "title",
        "idempotency_key",
        "idempotency_ttl_s",
        "idempotent",
    }
)


def idempotency_key(action_type: str, target: str, params: dict | None = None) -> str:
    """Stable idempotency key for a ``cortex_propose`` action (W1a).

    Identifies the ACTION, not the emission: two proposals requesting the
    same underlying action (same type, same target, same semantic params)
    produce the same key — so a re-issue, or a reply that narrates
    already-done work, collapses to a no-op at the executor's applied-keys
    ledger. See ``docs/architecture/IDEMPOTENT_BY_DESIGN.md``.

    Attach the result as ``payload.idempotency_key`` on the proposal. If an
    action is genuinely un-dedupable one-shot, do NOT fabricate a key —
    declare ``payload.idempotent = False`` instead (honesty flag).

    Args:
        action_type: the proposal type (e.g. ``"code_change_request"``).
        target: the target practice's canonical id
            (e.g. ``"empirica.david.empirica-cortex"``).
        params: the action's semantically-significant parameters. Volatile
            keys (``_VOLATILE_PARAM_KEYS`` — timestamps, nonces,
            per-emission ids, free-text prose) are dropped; the rest are
            canonicalized (sorted keys, tight separators) so key order and
            incidental fields don't change the result. Values must be
            JSON-serialisable; non-serialisable values fall back to ``str``.

    Returns:
        A hex sha256 digest.

    Raises:
        ValueError: if ``action_type`` or ``target`` is empty.
    """
    if not action_type:
        raise ValueError("action_type required")
    if not target:
        raise ValueError("target required")
    semantic = {k: v for k, v in (params or {}).items() if k not in _VOLATILE_PARAM_KEYS}
    canonical = json.dumps(
        {"action_type": action_type, "target": target, "params": semantic},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return compute_sha256(canonical.encode("utf-8"))


def truncate_to_cap(
    data: bytes,
    cap: int = DEFAULT_RENDER_SIZE_CAP,
    marker: bytes = b"\n\n[...TRUNCATED - content exceeded size cap]\n",
) -> tuple[bytes, bool]:
    """Truncate ``data`` to ``cap`` bytes; append a marker if cut.

    Returns ``(truncated_bytes, was_truncated)``. The marker is included
    in the returned bytes (not added to the cap; the marker may push the
    final length slightly past cap by the marker length).

    Mirrors the daemon's ``/api/v1/sources/{id}/content`` truncation
    pattern so the substrate behaves the same regardless of the
    promotion entrypoint.
    """
    if len(data) <= cap:
        return data, False
    return data[:cap] + marker, True

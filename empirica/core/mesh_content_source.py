"""Source-side MeshContent wrapper.

Thin adapter that exposes the MeshContent contract over an
``epistemic_sources`` row. Existing source-add / source-list /
sources-map continue to work unchanged on the raw row; this wrapper
is opt-in for callers that want the substrate view (canonical
address, normalized visibility, render-for-promotion).

Per `docs/architecture/MESH_CONTENT.md (not in this repo)` §7 Slice 1: wrapper, not a
schema change. Storage stays in `epistemic_sources` (per-project
SQLite). When future MeshContent primitives (SER mirror, shared
decision, etc.) need the same substrate, they get their own thin
wrapper alongside this one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from empirica.core.mesh_content import (
    DEFAULT_RENDER_SIZE_CAP,
    RenderResult,
    canonical_address,
    normalize_visibility,
    truncate_to_cap,
)


@dataclass
class MeshContentSource:
    """MeshContent view over an `epistemic_sources` row.

    Construct via :meth:`from_row` (from a SQLite Row / dict) so the
    wrapper layers cleanly over existing handlers without changing
    their query shape.

    Attributes:
        source_id: UUID from `epistemic_sources.id`
        practice_canonical: 3-level canonical practice id for the
            owning project (`<org>.<tenant>.<project>`). Caller passes
            this in — derivation lives in the practice-resolver, not
            here, so the wrapper stays storage-agnostic.
        title: source title
        description: source description (markdown, optional)
        source_url: external URL if web-sourced
        doc_path: local file path if file-sourced
        source_type: e.g. "document", "code", "web", "spec"
        visibility: normalized tier (`local` | `shared` | `public`)
        archived: True iff `epistemic_sources.archived = 1`
        created_by_actor: best-effort actor canonicalization (falls
            back to `discovered_by_ai` if no explicit actor field)
    """

    source_id: str
    practice_canonical: str
    title: str
    description: str | None = None
    source_url: str | None = None
    doc_path: str | None = None
    source_type: str = "document"
    visibility: str = "local"
    archived: bool = False
    created_by_actor: str | None = None

    # ── Constructors ──────────────────────────────────────────────────

    @classmethod
    def from_row(
        cls,
        row: Any,
        practice_canonical: str,
    ) -> MeshContentSource:
        """Build a wrapper from a SQLite Row or equivalent dict.

        Accepts any mapping-like row (dict, sqlite3.Row, namedtuple
        with `_asdict()`). Missing optional fields fall back to safe
        defaults; unknown visibility values normalize to `shared` per
        the safety invariant.

        Practice canonical comes in as an argument because deriving it
        requires a roster lookup that belongs in the practice resolver,
        not in this wrapper.
        """

        def _get(key: str, default: Any = None) -> Any:
            try:
                return row[key]
            except (KeyError, IndexError):
                pass
            if hasattr(row, "get"):
                return row.get(key, default)
            if hasattr(row, "_asdict"):
                return row._asdict().get(key, default)
            return default

        return cls(
            source_id=_get("id") or _get("source_id"),
            practice_canonical=practice_canonical,
            title=_get("title") or "",
            description=_get("description"),
            source_url=_get("source_url"),
            doc_path=_get("doc_path") or _get("path"),
            source_type=_get("source_type") or "document",
            visibility=normalize_visibility(_get("visibility")),
            archived=bool(_get("archived", 0)),
            created_by_actor=_get("created_by_actor") or _get("discovered_by_ai"),
        )

    # ── MeshContent contract ─────────────────────────────────────────

    def canonical_address(self) -> str:
        """The cross-mesh address for this source.

        Format: `<practice>~src_<uuid>` (separator `~` per cortex's
        transport-safety analysis; see `mesh_content.CANONICAL_SEPARATOR`).
        When the wider format thread locks any change, only
        `empirica.core.mesh_content.canonical_address` updates; this
        wrapper is unchanged.
        """
        return canonical_address(
            practice=self.practice_canonical,
            content_type="src",
            uuid_str=self.source_id,
        )

    def render(self, size_cap: int = DEFAULT_RENDER_SIZE_CAP) -> RenderResult:
        """Render the canonical bytes for mesh promotion.

        Returns the markdown payload that cortex-side storage would
        accept for content promotion. Inline assembly from title +
        description + URL/path metadata. **Does not** fetch the
        external URL or read the local file — that's promotion's job
        (and the daemon's `GET /api/v1/sources/{id}/content` endpoint
        already covers file resolution).

        For sources where the description IS the canonical material
        (e.g. captured transcripts, pasted snippets), this render
        result is the substrate-promotable payload. For sources where
        the external URL/file is the material, callers should chain
        through the daemon's content endpoint and render those bytes
        instead.
        """
        parts: list[str] = [f"# {self.title}\n"]
        if self.description:
            parts.append(self.description.strip())
            parts.append("")
        meta_lines: list[str] = []
        meta_lines.append(f"**Source type:** {self.source_type}")
        meta_lines.append(f"**Visibility:** {self.visibility}")
        if self.source_url:
            meta_lines.append(f"**URL:** {self.source_url}")
        if self.doc_path:
            meta_lines.append(f"**Path:** {self.doc_path}")
        if self.created_by_actor:
            meta_lines.append(f"**Created by:** {self.created_by_actor}")
        meta_lines.append(f"**Canonical address:** `{self.canonical_address()}`")
        parts.append("\n".join(meta_lines))
        payload = "\n".join(parts).encode("utf-8")
        truncated_bytes, was_truncated = truncate_to_cap(payload, cap=size_cap)
        return RenderResult(
            canonical_bytes=truncated_bytes,
            content_type="text/markdown; charset=utf-8",
            size_bytes=len(payload),
            truncated=was_truncated,
            metadata={
                "source_id": self.source_id,
                "practice": self.practice_canonical,
                "visibility": self.visibility,
            },
        )

    def is_shareable(self) -> bool:
        """True iff the source's visibility tier permits mesh promotion.

        Convenience predicate used by the daemon-push-on-visibility-change
        promotion path: only `shared` / `public` sources promote; `local`
        stays local.
        """
        return self.visibility in ("shared", "public")

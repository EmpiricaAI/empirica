"""Visibility tiers for epistemic artifacts (Phase 0 of PROPOSAL_VISIBILITY_TIERS.md).

Three tiers govern where artifacts can travel:

- ``public``  — publicly shareable; safe to push to a public repo.
- ``shared``  — team-private, co-versioned (the default). Phase 1 will
  encrypt these via git-crypt; Phase 0 is metadata-only.
- ``local``   — machine-local, never shared (raw secrets, session state).

Phase 0 stores the tier in a dedicated ``visibility`` column on each artifact
table. Validation lives here in Python because SQLite ALTER TABLE ADD COLUMN
does not support CHECK constraints on existing rows; the CLI and repository
layer normalize input through :func:`normalize_visibility` before persisting.
"""

from typing import Literal

VisibilityTier = Literal["public", "shared", "local"]

VISIBILITY_TIERS: tuple[str, ...] = ("public", "shared", "local")

DEFAULT_VISIBILITY: str = "local"


def normalize_visibility(value: str | None) -> str:
    """Return a valid tier or fall back to the default.

    ``None`` and unknown values both resolve to ``DEFAULT_VISIBILITY`` ('local').

    'local' is the fail-closed direction, and the previous default ('shared')
    was a doctrine defect, not a decision: the system prompt has always said
    "local (default) — stays in this project only", while this constant said
    'shared' — and under an active L2 agreement, 'shared' IS cross-tenant
    exposure. The old docstring justified shared-on-None as "never accidentally
    promote to public", which guards the wrong boundary. Measured cost before
    the fix, David's stores alone: core 4,289 shared + 77 public, autonomy 553
    shared, workspace (CRM rows) 389 shared + 34 public — overwhelmingly
    unflagged-defaulted, not deliberate. Sharing is an OPT-IN act; a default
    cannot perform it. (Autonomy prop_7ptj5rbnybgmbejrhr5idbzbdy; historical
    rows deliberately untouched — that adjudication is David's, and a mass
    change here would conflate the defect fix with the ruling.)
    """
    if value is None:
        return DEFAULT_VISIBILITY
    v = str(value).strip().lower()
    if v in VISIBILITY_TIERS:
        return v
    return DEFAULT_VISIBILITY

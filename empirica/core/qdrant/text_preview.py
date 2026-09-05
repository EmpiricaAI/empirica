"""
Preview/full pairs for embedded payload text.

Every Qdrant payload in this codebase stores a long string twice: a capped
`<field>` for ranking and display, and a `<field>_full` holding the whole
thing. The pair only works if `_full` is populated when the text OVERFLOWS —
which is the one case the cap loses data.

Ten sites had the condition inverted, setting `_full` only when the text
already fitted in the preview. That is null in exactly the case the field
exists for, so the overflow was simply gone. Measured 2026-08-21 on
`global_learnings`: 483 of 991 points. Measured 2026-09-05 on one practice's
`memory` collection: 2,507 of 5,833 — 43% carrying a 500-char cut with no
full copy anywhere and nothing saying so.

The vectors are unaffected: every caller embeds the full text before building
the payload. What was lost is the text a reader gets BACK, which is why the
damage is invisible to similarity search and shows up only when someone reads
a result and finds it stops mid-sentence.

`preview_fields` exists so site eleven cannot get it wrong.
"""

from __future__ import annotations

#: Default cap for a preview field. Callers with a different budget pass it in.
DEFAULT_PREVIEW = 500


def preview_fields(name: str, text: str | None, limit: int = DEFAULT_PREVIEW) -> dict:
    """
    Build the `{name}` / `{name}_full` / `{name}_truncated` triple.

    `{name}`            capped at `limit`, for ranking and display
    `{name}_full`       the whole string, present ONLY when it overflows
                        (when it fits, `{name}` already holds it)
    `{name}_truncated`  so a consumer can tell a short complete string from a
                        long cut one without re-deriving it from lengths

    The truncation flag is the load-bearing half: a preview that does not
    declare itself a preview is indistinguishable from a whole thought.
    """
    if not text:
        return {name: None, f"{name}_full": None, f"{name}_truncated": False}
    over = len(text) > limit
    return {
        name: text[:limit],
        f"{name}_full": text if over else None,
        f"{name}_truncated": over,
    }

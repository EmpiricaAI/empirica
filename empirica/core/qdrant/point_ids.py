"""Canonical Qdrant point-id derivation.

Writers and deleters MUST agree on how an artifact's UUID becomes a numeric
Qdrant point id. They did not: every writer used a 15-hex-digit md5 prefix while
``delete-artifacts`` used 16 digits plus ``% 2**63``, so the deleter addressed a
point that had never existed and Qdrant — which answers a delete of an absent
point with ``status: completed`` — reported success (issue #412).

The 15-digit form is load-bearing: it is what is already on disk in every
deployed collection. Changing it would orphan every vector ever written, so this
helper preserves it exactly rather than "improving" it.
"""

from __future__ import annotations

import hashlib

# 15 hex digits = 60 bits, comfortably inside Qdrant's unsigned-64 point-id range,
# so no modulo is needed (and applying one would change every existing id).
_ID_HEX_WIDTH = 15


def artifact_point_id(artifact_id: str) -> int:
    """Return the Qdrant point id for ``artifact_id``.

    The single source of truth for the mapping. Any code that writes, reads or
    deletes an artifact's vector must route through this function.
    """
    digest = hashlib.md5(artifact_id.encode(), usedforsecurity=False).hexdigest()
    return int(digest[:_ID_HEX_WIDTH], 16)

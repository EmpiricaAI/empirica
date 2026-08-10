"""GH #405: live log and bulk re-embed must agree on a mistake's point identity.

The Qdrant point id is md5 of the string id, so live writing bare ``<uuid>``
while rebuild wrote ``mistake_<uuid>`` meant an upsert from one path never
replaced the other's point — every rebuild added a second copy of every
live-logged mistake, and the texts differed too (live added a ``MISTAKE: ``
prefix), so the copies did not even dedupe by content. Retrieval had already
documented the prefix half (pattern_retrieval.py:30-40) — the reader coping
with a writer divergence instead of the writers agreeing.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_live_path_uses_the_bulk_point_id_convention():
    """THE FIX: one string identity, hashed to one point, overwritten by either path."""
    src = (REPO / "empirica/cli/command_handlers/artifact_log_commands.py").read_text()
    assert 'item_id=f"mistake_{mistake_id}"' in src, "live path writes a bare uuid — rebuild cannot overwrite it"


def test_live_path_no_longer_prefixes_the_text():
    """Text must match bulk's build_mistake_text output so copies dedupe."""
    src = (REPO / "empirica/cli/command_handlers/artifact_log_commands.py").read_text()
    assert "prefix=True" not in src, "a writer still adds the MISTAKE: prefix bulk does not"


def test_both_bulk_paths_still_use_the_same_convention():
    for rel in ("empirica/core/qdrant/rebuild.py", "empirica/cli/command_handlers/project_embed.py"):
        src = (REPO / rel).read_text()
        assert 'f"mistake_{' in src, f"{rel} left the convention"


def test_the_parser_still_reads_historical_prefixed_points():
    """Points embedded by the OLD live path carry the prefix forever; the reader
    must keep tolerating them or every pre-fix mistake goes dark."""
    from empirica.core.mistake_text import parse_mistake_text

    m, p = parse_mistake_text("MISTAKE: did the thing Prevention: check first")
    assert m == "did the thing"
    assert p == "check first"

    m2, p2 = parse_mistake_text("did the thing Prevention: check first")
    assert (m2, p2) == (m, p), "prefixed and unprefixed forms must parse identically"

"""Tests for the MeshContent substrate (goal d7efea49 Slice 1).

Per `docs/architecture/MESH_CONTENT.md` §7: the thin first slice ships
the module + Source-side wrapper neutral on the canonical-address
format (still pending `prop_w7q24hdurnhfnasf2ahiq5gvte`). Tests lock
the contract so the rest of the surface stays stable when the
addressing thread lands and only `canonical_address()` changes.

Coverage:
  Address:
    1. canonical_address round-trips via parse_canonical_address
    2. parse rejects malformed addresses (empty / missing # / missing _ / unknown type)
    3. canonical_address rejects empty practice / empty uuid / unknown type

  Render pipeline:
    4. RenderResult auto-computes size_bytes + sha256
    5. truncate_to_cap leaves small payloads alone
    6. truncate_to_cap appends truncation marker for over-cap payloads
    7. compute_sha256 is deterministic + hex

  Source wrapper:
    8. from_row hydrates from a sqlite3.Row-like dict with defaults
    9. visibility normalizes (bogus → 'shared', case-insensitive)
   10. canonical_address composes correctly from wrapper fields
   11. is_shareable True for shared/public, False for local
   12. render emits a markdown payload with canonical address + visibility
   13. render respects size cap + flags truncated
"""

from __future__ import annotations

import sqlite3

import pytest

from empirica.core.mesh_content import (
    DEFAULT_RENDER_SIZE_CAP,
    MESH_CONTENT_TYPES,
    RenderResult,
    canonical_address,
    compute_sha256,
    idempotency_key,
    parse_canonical_address,
    truncate_to_cap,
)
from empirica.core.mesh_content_source import MeshContentSource

# ── Address ────────────────────────────────────────────────────────────


def test_canonical_address_round_trip():
    practice = "empirica.david.empirica"
    uuid_str = "11111111-2222-3333-4444-555555555555"
    addr = canonical_address(practice, "src", uuid_str)
    assert addr == f"{practice}~src_{uuid_str}"
    assert parse_canonical_address(addr) == (practice, "src", uuid_str)


def test_canonical_address_uses_tilde_separator():
    """Locks `~` as the cross-mesh separator (transport-safe per cortex's
    pushback on `#`/`::`/etc. — see CANONICAL_SEPARATOR docstring)."""
    from empirica.core.mesh_content import CANONICAL_SEPARATOR

    assert CANONICAL_SEPARATOR == "~"
    addr = canonical_address("org.tenant.proj", "src", "abc")
    assert "~" in addr
    assert "#" not in addr
    assert "::" not in addr


def test_canonical_address_rejects_empty_practice():
    with pytest.raises(ValueError, match="practice required"):
        canonical_address("", "src", "abc")


def test_canonical_address_rejects_empty_uuid():
    with pytest.raises(ValueError, match="uuid required"):
        canonical_address("empirica.david.empirica", "src", "")


def test_canonical_address_rejects_unknown_type():
    with pytest.raises(ValueError, match="unknown content_type"):
        canonical_address("empirica.david.empirica", "bogus", "abc")


def test_canonical_address_accepts_all_registered_types():
    """Every type in MESH_CONTENT_TYPES must work — guard against
    registry drift."""
    for t in MESH_CONTENT_TYPES:
        addr = canonical_address("empirica.david.empirica", t, "uuid-x")
        assert t in addr
        assert parse_canonical_address(addr)[1] == t


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "no-hash",
        "practice~no-underscore",
        "~src_abc",  # empty practice before separator
        "practice~_abc",  # empty type
        "practice~src_",  # empty uuid
        "practice~bogus_abc",  # unknown type
    ],
)
def test_parse_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_canonical_address(bad)


# ── Render pipeline ────────────────────────────────────────────────────


def test_render_result_auto_size_and_sha():
    r = RenderResult(canonical_bytes=b"hello")
    assert r.size_bytes == 5
    assert r.sha256 == compute_sha256(b"hello")
    assert r.truncated is False


def test_render_result_preserves_explicit_overrides():
    """Caller can pre-set size_bytes / sha256 (e.g. for pre-truncation
    metadata) and the defaults won't clobber."""
    r = RenderResult(
        canonical_bytes=b"hi",
        size_bytes=999,
        sha256="custom",
    )
    assert r.size_bytes == 999
    assert r.sha256 == "custom"


def test_truncate_to_cap_below_cap_unchanged():
    data = b"x" * 100
    out, was_truncated = truncate_to_cap(data, cap=200)
    assert out == data
    assert was_truncated is False


def test_truncate_to_cap_above_cap_appends_marker():
    data = b"x" * 100
    out, was_truncated = truncate_to_cap(data, cap=50)
    assert was_truncated is True
    assert out.startswith(b"x" * 50)
    assert b"TRUNCATED" in out


def test_compute_sha256_deterministic():
    assert compute_sha256(b"hello") == compute_sha256(b"hello")
    assert compute_sha256(b"hello") != compute_sha256(b"world")
    # All hex
    assert all(c in "0123456789abcdef" for c in compute_sha256(b"hello"))
    assert len(compute_sha256(b"hello")) == 64


# ── Source wrapper ─────────────────────────────────────────────────────


@pytest.fixture
def sqlite_row():
    """A sqlite3.Row mirroring the post-049 epistemic_sources columns."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE epistemic_sources (
            id TEXT,
            title TEXT,
            description TEXT,
            source_url TEXT,
            source_type TEXT,
            visibility TEXT,
            archived BOOLEAN,
            discovered_by_ai TEXT
        )
    """)
    conn.execute(
        "INSERT INTO epistemic_sources VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "src-uuid-1",
            "RFC 7519",
            "JWT spec",
            "https://datatracker.ietf.org/doc/html/rfc7519",
            "spec",
            "shared",
            0,
            "claude-code",
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM epistemic_sources").fetchone()
    yield row
    conn.close()


def test_from_row_hydrates_post_049_columns(sqlite_row):
    mcs = MeshContentSource.from_row(
        sqlite_row,
        practice_canonical="empirica.david.empirica",
    )
    assert mcs.source_id == "src-uuid-1"
    assert mcs.title == "RFC 7519"
    assert mcs.description == "JWT spec"
    assert mcs.source_url == "https://datatracker.ietf.org/doc/html/rfc7519"
    assert mcs.source_type == "spec"
    assert mcs.visibility == "shared"
    assert mcs.archived is False
    assert mcs.created_by_actor == "claude-code"
    assert mcs.practice_canonical == "empirica.david.empirica"


def test_from_row_normalizes_bogus_visibility():
    """Bogus visibility value must collapse to default — never silently
    promote to public (safety invariant inherited from
    normalize_visibility)."""
    bogus_row = {
        "id": "abc",
        "title": "T",
        "visibility": "top-secret",
    }
    mcs = MeshContentSource.from_row(
        bogus_row,
        practice_canonical="empirica.david.empirica",
    )
    assert mcs.visibility == "shared"  # the safe default per normalize_visibility


def test_from_row_handles_missing_optional_fields():
    """Sparse rows (e.g. legacy entries without all columns) hydrate
    with safe defaults."""
    sparse = {"id": "abc", "title": "T"}
    mcs = MeshContentSource.from_row(
        sparse,
        practice_canonical="empirica.david.empirica",
    )
    assert mcs.source_type == "document"
    assert mcs.archived is False
    assert mcs.created_by_actor is None


def test_canonical_address_composition():
    mcs = MeshContentSource(
        source_id="abc-123",
        practice_canonical="empirica.david.empirica",
        title="T",
    )
    assert mcs.canonical_address() == "empirica.david.empirica~src_abc-123"


@pytest.mark.parametrize(
    "tier,expected",
    [
        ("local", False),
        ("shared", True),
        ("public", True),
    ],
)
def test_is_shareable_per_tier(tier, expected):
    mcs = MeshContentSource(
        source_id="abc",
        practice_canonical="x",
        title="T",
        visibility=tier,
    )
    assert mcs.is_shareable() is expected


def test_render_emits_markdown_with_address_and_visibility():
    mcs = MeshContentSource(
        source_id="abc-123",
        practice_canonical="empirica.david.empirica",
        title="Test source",
        description="Body content here",
        source_type="document",
        visibility="shared",
        source_url="https://example.com",
        created_by_actor="claude-code",
    )
    result = mcs.render()
    payload = result.canonical_bytes.decode("utf-8")
    assert result.truncated is False
    assert result.content_type.startswith("text/markdown")
    assert "Test source" in payload
    assert "Body content here" in payload
    assert "empirica.david.empirica~src_abc-123" in payload
    assert "Visibility:** shared" in payload
    assert "https://example.com" in payload
    assert result.sha256  # auto-computed
    assert result.metadata["source_id"] == "abc-123"
    assert result.metadata["visibility"] == "shared"


def test_render_respects_size_cap():
    """A description larger than the cap gets truncated + flagged."""
    huge_desc = "x" * (DEFAULT_RENDER_SIZE_CAP * 2)
    mcs = MeshContentSource(
        source_id="abc",
        practice_canonical="empirica.david.empirica",
        title="Huge",
        description=huge_desc,
    )
    result = mcs.render(size_cap=1024)  # tighter cap for the test
    assert result.truncated is True
    assert b"TRUNCATED" in result.canonical_bytes
    # size_bytes is the PRE-truncation length
    assert result.size_bytes > 1024


def test_render_minimal_source():
    """No description / no URL / no actor → still renders cleanly with
    just the title + visibility + canonical address."""
    mcs = MeshContentSource(
        source_id="abc",
        practice_canonical="empirica.david.empirica",
        title="Just a title",
    )
    result = mcs.render()
    payload = result.canonical_bytes.decode("utf-8")
    assert "Just a title" in payload
    assert "Visibility:** local" in payload  # default
    assert "src_abc" in payload


# ── idempotency_key (W1a) ─────────────────────────────────────────────


def test_idempotency_key_deterministic():
    """Same action → same key (the whole point)."""
    a = idempotency_key("code_change_request", "empirica.david.empirica-cortex", {"path": "x.py", "symbol": "f"})
    b = idempotency_key("code_change_request", "empirica.david.empirica-cortex", {"path": "x.py", "symbol": "f"})
    assert a == b
    assert len(a) == 64 and all(c in "0123456789abcdef" for c in a)  # hex sha256


def test_idempotency_key_param_order_independent():
    """Key order in params must not change the key (canonicalization)."""
    a = idempotency_key("t", "target", {"path": "x.py", "symbol": "f"})
    b = idempotency_key("t", "target", {"symbol": "f", "path": "x.py"})
    assert a == b


def test_idempotency_key_drops_volatile_fields():
    """Incidental fields (timestamp, nonce, proposal_id, summary) don't change identity."""
    base = idempotency_key("t", "target", {"path": "x.py"})
    noisy = idempotency_key(
        "t",
        "target",
        {"path": "x.py", "timestamp": 12345, "nonce": "abc", "proposal_id": "prop_z", "summary": "re-phrased"},
    )
    assert base == noisy


def test_idempotency_key_differs_on_real_change():
    """Different type / target / semantic params → different keys."""
    base = idempotency_key("code_change_request", "empirica.david.empirica-cortex", {"symbol": "f"})
    assert base != idempotency_key("architecture_decision", "empirica.david.empirica-cortex", {"symbol": "f"})
    assert base != idempotency_key("code_change_request", "empirica.david.empirica-workspace", {"symbol": "f"})
    assert base != idempotency_key("code_change_request", "empirica.david.empirica-cortex", {"symbol": "g"})


def test_idempotency_key_none_params():
    """None params is valid and distinct from a populated action."""
    k = idempotency_key("investigation_request", "empirica.david.empirica-cortex")
    assert len(k) == 64
    assert k != idempotency_key("investigation_request", "empirica.david.empirica-cortex", {"query": "x"})


@pytest.mark.parametrize("bad", [("", "target"), ("t", "")])
def test_idempotency_key_rejects_empty(bad):
    with pytest.raises(ValueError):
        idempotency_key(bad[0], bad[1], {"a": 1})

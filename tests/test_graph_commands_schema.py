"""Tests for batch artifact schema discoverability.

Covers the three forgiving fixes added to log-artifacts/resolve-artifacts/
delete-artifacts after AIs (including this one) repeatedly tripped on
'id' vs 'ref' and 'type' vs 'relation':

  1. --schema flag prints JSON shape and exits 0 (no DB needed)
  2. _normalize_graph accepts 'id' as alias for 'ref' on nodes,
     'type' as alias for 'relation' on edges
  3. Validation errors include a hint pointing at --schema
"""

from __future__ import annotations

from empirica.cli.command_handlers import graph_commands as gc

# ─── _normalize_graph ─────────────────────────────────────────────────────


def test_normalize_passes_canonical_unchanged():
    g = {
        "nodes": [{"ref": "f1", "type": "finding", "data": {"finding": "x"}}],
        "edges": [{"from": "f1", "to": "f2", "relation": "evidence"}],
    }
    out, warnings = gc._normalize_graph(g)
    assert out["nodes"][0]["ref"] == "f1"
    assert out["edges"][0]["relation"] == "evidence"
    assert warnings == []


def test_normalize_accepts_id_alias_for_ref():
    g = {
        "nodes": [{"id": "f1", "type": "finding", "data": {"finding": "x"}}],
        "edges": [],
    }
    out, warnings = gc._normalize_graph(g)
    assert out["nodes"][0]["ref"] == "f1"
    assert any("ref" in w and "id" in w for w in warnings)


def test_normalize_accepts_node_id_alias_for_ref():
    g = {
        "nodes": [{"node_id": "f1", "type": "finding", "data": {"finding": "x"}}],
        "edges": [],
    }
    out, _ = gc._normalize_graph(g)
    assert out["nodes"][0]["ref"] == "f1"


def test_normalize_accepts_type_alias_for_relation():
    g = {
        "nodes": [
            {"ref": "f1", "type": "finding", "data": {"finding": "x"}},
            {"ref": "f2", "type": "finding", "data": {"finding": "y"}},
        ],
        "edges": [{"from": "f1", "to": "f2", "type": "evidence"}],
    }
    out, warnings = gc._normalize_graph(g)
    assert out["edges"][0]["relation"] == "evidence"
    assert any("relation" in w and "type" in w for w in warnings)


def test_normalize_accepts_kind_alias_for_relation():
    g = {
        "nodes": [],
        "edges": [{"from": "a", "to": "b", "kind": "evidence"}],
    }
    out, _ = gc._normalize_graph(g)
    assert out["edges"][0]["relation"] == "evidence"


def test_normalize_canonical_wins_over_alias():
    """If both ref and id are present, ref is preserved unchanged."""
    g = {
        "nodes": [{"ref": "r1", "id": "i1", "type": "finding", "data": {"finding": "x"}}],
        "edges": [],
    }
    out, warnings = gc._normalize_graph(g)
    assert out["nodes"][0]["ref"] == "r1"
    # No warning since canonical was used
    assert warnings == []


def test_normalize_handles_non_dict_input_gracefully():
    out, warnings = gc._normalize_graph(["not", "a", "graph"])  # type: ignore[arg-type]
    assert out == ["not", "a", "graph"]
    assert warnings == []


def test_normalize_warnings_deduplicated():
    """Multiple nodes using the same alias produce one warning, not N."""
    g = {
        "nodes": [
            {"id": "a", "type": "finding", "data": {"finding": "x"}},
            {"id": "b", "type": "finding", "data": {"finding": "y"}},
            {"id": "c", "type": "finding", "data": {"finding": "z"}},
        ],
        "edges": [],
    }
    _, warnings = gc._normalize_graph(g)
    assert len(warnings) == 1


# ─── _validate_graph still works after normalization ──────────────────────


def test_validate_passes_normalized_input():
    g = {"nodes": [{"id": "f1", "type": "finding", "data": {"finding": "x"}}], "edges": []}
    out, _ = gc._normalize_graph(g)
    errors = gc._validate_graph(out)
    assert errors == []


def test_validate_still_rejects_truly_missing_ref():
    """If neither ref nor any alias is present, validation fails."""
    g = {"nodes": [{"type": "finding", "data": {"finding": "x"}}], "edges": []}
    out, _ = gc._normalize_graph(g)
    errors = gc._validate_graph(out)
    assert any("missing" in e and "ref" in e for e in errors)


# ─── --schema flag ─────────────────────────────────────────────────────────


class _Args:
    """Minimal args namespace for handler smoke tests."""

    def __init__(self, **kwargs):
        self.schema = False
        self.config = "-"
        self.verbose = False
        self.output = "json"
        for k, v in kwargs.items():
            setattr(self, k, v)


def test_log_artifacts_schema_flag_short_circuits(capsys):
    args = _Args(schema=True)
    rc = gc.handle_log_artifacts_command(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert '"command": "log-artifacts"' in captured.out
    assert '"valid_node_types"' in captured.out
    assert '"valid_relations"' in captured.out


def test_resolve_artifacts_schema_flag_short_circuits(capsys):
    args = _Args(schema=True)
    rc = gc.handle_resolve_artifacts_command(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert '"command": "resolve-artifacts"' in captured.out
    assert '"resolutions"' in captured.out


def test_delete_artifacts_schema_flag_short_circuits(capsys):
    args = _Args(schema=True, dry_run=False)
    rc = gc.handle_delete_artifacts_command(args)
    assert rc == 0
    captured = capsys.readouterr()
    assert '"command": "delete-artifacts"' in captured.out
    assert '"deletions"' in captured.out


# ─── error message hint ───────────────────────────────────────────────────


def test_validation_error_response_includes_schema_hint(monkeypatch, capsys):
    """When validation fails, the response should point at --schema."""
    import io

    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO('{"nodes": [{"type": "finding", "data": {"finding": "x"}}]}'),
    )
    args = _Args()
    result = gc._read_graph_input(args)
    assert result is None
    captured = capsys.readouterr()
    assert "--schema" in captured.out
    assert "'id'" in captured.out  # mentions the common pitfall


# ─── bead retirement (2026-06-02) ─────────────────────────────────────────
# Bead v0 was retired three-way (cortex/empirica/extension) on 2026-06-01.
# Cross-practitioner coordination state moved to cortex-resident SER
# (Shared Epistemic Record). The v0 bead node type + 4 net-new edges
# (tracks/owned_by/about/worked_by) are gone from empirica's graph schema;
# cortex's EDGE_RELATIONS no longer accepts them either. These tests assert
# the retirement is complete on empirica's side so the schema can't drift
# back in without an intentional revert.


def test_bead_node_type_retired():
    """`bead` is NOT in NODE_REQUIRED_FIELDS — retired post-SER convergence."""
    assert "bead" not in gc.NODE_REQUIRED_FIELDS


def test_bead_v0_edges_retired():
    """The 4 bead-courier edges are out of VALID_RELATIONS.

    Pre-existing relations stay; cross-practitioner role semantics now live
    on cortex's SER participants table, not as edge metadata on bead nodes.
    """
    for rel in ("tracks", "owned_by", "about", "worked_by"):
        assert rel not in gc.VALID_RELATIONS, f"bead v0 relation still present: {rel}"
    # Pre-existing relations untouched.
    for rel in ("attached_to", "sourced_from", "evidence"):
        assert rel in gc.VALID_RELATIONS


def test_bead_out_of_creation_order():
    """Bead retired from CREATION_ORDER."""
    assert "bead" not in gc.CREATION_ORDER


def test_log_artifacts_schema_no_longer_advertises_bead():
    """The printable schema (--schema output) doesn't advertise bead."""
    schema_str = str(gc.LOG_ARTIFACTS_SCHEMA)
    assert "bead" not in schema_str
    for rel in ("tracks", "owned_by", "about", "worked_by"):
        assert rel not in schema_str


# ─── GH #402: a success receipt for a no-op ────────────────────────────


class TestUnrecognisedTopLevelKeyIsRejected:
    """`resolve-artifacts` returned ok:true, resolved:0, errors:[] for a payload
    whose top-level key was not part of the schema — nothing happened and
    nothing said so. The error is the only surface that can teach the schema,
    since a wrong key produces no rejection anywhere else (GH #402)."""

    def _run(self, payload: str):
        import json as _json
        import subprocess

        p = subprocess.run(["empirica", "resolve-artifacts", "-"], input=payload, capture_output=True, text=True)
        return p.returncode, _json.loads(p.stdout)

    def test_unknown_top_level_key_returns_ok_false(self):
        rc, out = self._run('{"unknowns":[{"id":"deadbeef","resolution":"probe"}]}')
        assert out["ok"] is False, "a no-op must not report success"
        assert rc == 1
        assert out["unknown_keys"] == ["unknowns"]
        assert "resolutions" in out["error"], "the error must teach the accepted schema"

    def test_documented_filter_mode_still_accepted(self):
        _rc, out = self._run('{"filter":{"type":"finding","matching":"zzz-no-match-%"},"resolution":"x","apply":false}')
        assert out["ok"] is True

    def test_empty_resolutions_list_is_not_flagged_as_unknown_key(self):
        """An empty documented payload is a different (pre-existing) behaviour —
        the unknown-key guard must only fire on genuinely unrecognised keys."""
        _rc, out = self._run('{"resolutions": []}')
        assert "unknown_keys" not in out

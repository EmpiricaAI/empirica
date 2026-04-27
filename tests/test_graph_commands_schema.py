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
        'nodes': [{'ref': 'f1', 'type': 'finding', 'data': {'finding': 'x'}}],
        'edges': [{'from': 'f1', 'to': 'f2', 'relation': 'evidence'}],
    }
    out, warnings = gc._normalize_graph(g)
    assert out['nodes'][0]['ref'] == 'f1'
    assert out['edges'][0]['relation'] == 'evidence'
    assert warnings == []


def test_normalize_accepts_id_alias_for_ref():
    g = {
        'nodes': [{'id': 'f1', 'type': 'finding', 'data': {'finding': 'x'}}],
        'edges': [],
    }
    out, warnings = gc._normalize_graph(g)
    assert out['nodes'][0]['ref'] == 'f1'
    assert any('ref' in w and 'id' in w for w in warnings)


def test_normalize_accepts_node_id_alias_for_ref():
    g = {
        'nodes': [{'node_id': 'f1', 'type': 'finding', 'data': {'finding': 'x'}}],
        'edges': [],
    }
    out, _ = gc._normalize_graph(g)
    assert out['nodes'][0]['ref'] == 'f1'


def test_normalize_accepts_type_alias_for_relation():
    g = {
        'nodes': [
            {'ref': 'f1', 'type': 'finding', 'data': {'finding': 'x'}},
            {'ref': 'f2', 'type': 'finding', 'data': {'finding': 'y'}},
        ],
        'edges': [{'from': 'f1', 'to': 'f2', 'type': 'evidence'}],
    }
    out, warnings = gc._normalize_graph(g)
    assert out['edges'][0]['relation'] == 'evidence'
    assert any('relation' in w and 'type' in w for w in warnings)


def test_normalize_accepts_kind_alias_for_relation():
    g = {
        'nodes': [],
        'edges': [{'from': 'a', 'to': 'b', 'kind': 'evidence'}],
    }
    out, _ = gc._normalize_graph(g)
    assert out['edges'][0]['relation'] == 'evidence'


def test_normalize_canonical_wins_over_alias():
    """If both ref and id are present, ref is preserved unchanged."""
    g = {
        'nodes': [{'ref': 'r1', 'id': 'i1', 'type': 'finding',
                   'data': {'finding': 'x'}}],
        'edges': [],
    }
    out, warnings = gc._normalize_graph(g)
    assert out['nodes'][0]['ref'] == 'r1'
    # No warning since canonical was used
    assert warnings == []


def test_normalize_handles_non_dict_input_gracefully():
    out, warnings = gc._normalize_graph(['not', 'a', 'graph'])  # type: ignore[arg-type]
    assert out == ['not', 'a', 'graph']
    assert warnings == []


def test_normalize_warnings_deduplicated():
    """Multiple nodes using the same alias produce one warning, not N."""
    g = {
        'nodes': [
            {'id': 'a', 'type': 'finding', 'data': {'finding': 'x'}},
            {'id': 'b', 'type': 'finding', 'data': {'finding': 'y'}},
            {'id': 'c', 'type': 'finding', 'data': {'finding': 'z'}},
        ],
        'edges': [],
    }
    _, warnings = gc._normalize_graph(g)
    assert len(warnings) == 1


# ─── _validate_graph still works after normalization ──────────────────────

def test_validate_passes_normalized_input():
    g = {'nodes': [{'id': 'f1', 'type': 'finding', 'data': {'finding': 'x'}}],
         'edges': []}
    out, _ = gc._normalize_graph(g)
    errors = gc._validate_graph(out)
    assert errors == []


def test_validate_still_rejects_truly_missing_ref():
    """If neither ref nor any alias is present, validation fails."""
    g = {'nodes': [{'type': 'finding', 'data': {'finding': 'x'}}], 'edges': []}
    out, _ = gc._normalize_graph(g)
    errors = gc._validate_graph(out)
    assert any('missing' in e and 'ref' in e for e in errors)


# ─── --schema flag ─────────────────────────────────────────────────────────

class _Args:
    """Minimal args namespace for handler smoke tests."""
    def __init__(self, **kwargs):
        self.schema = False
        self.config = '-'
        self.verbose = False
        self.output = 'json'
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
        'sys.stdin',
        io.StringIO('{"nodes": [{"type": "finding", "data": {"finding": "x"}}]}'),
    )
    args = _Args()
    result = gc._read_graph_input(args)
    assert result is None
    captured = capsys.readouterr()
    assert '--schema' in captured.out
    assert "'id'" in captured.out  # mentions the common pitfall

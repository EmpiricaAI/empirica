"""Output-contract guard — cross-field invariants on canonical projections.

Catches the *null/empty-masking* silent-failure class: a count that diverges
from its list, or a declared-non-null field that is silently ``None`` (e.g.
wrong ``.get`` keys yielding rows whose key fields are all ``None`` while
exit-0 + a valid shape hide it — exactly the compliance-report "12 PASS rows,
all check-names None" and the entity-list "count vs entities" smells).

The invariants are *universal* — ``count == len(list)`` and required fields
present-and-non-None on every item. The registered tests below are the
*substrate-specific* part: add one when a projection grows a count+list pair or
a non-null contract. Most handlers write ``count=len(list)`` in source (so a
static check is trivially true); the value here is exercising the REAL handler
against SEEDED data so a runtime divergence — a different query for the count
than the list, a masked field — fails loud.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import empirica.data.repositories.workspace_db as wdb
from empirica.cli.command_handlers import entity_commands
from empirica.data.repositories.workspace_db import WorkspaceDBRepository

# ── universal invariants ────────────────────────────────────────────────


def assert_count_matches_list(payload: dict, count_key: str, list_key: str) -> None:
    """A count field must equal the length of its sibling list."""
    assert count_key in payload, f"missing count key '{count_key}' in payload"
    assert list_key in payload, f"missing list key '{list_key}' in payload"
    n, items = payload[count_key], payload[list_key]
    assert n == len(items), (
        f"contract violation: {count_key}={n} != len({list_key})={len(items)} "
        "— a count diverging from its list is the 'null/empty masking' smell"
    )


def assert_items_nonnull(items: list[dict], required: list[str]) -> None:
    """Every item must carry the declared fields, none silently ``None``."""
    for i, item in enumerate(items):
        for field in required:
            assert item.get(field) is not None, (
                f"contract violation: item[{i}].{field} is None — a silently-masked field"
            )


# ── fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def seeded_workspace(tmp_path, monkeypatch):
    """An isolated workspace.db seeded with 3 contact entities."""
    dbp = tmp_path / "workspace.db"
    monkeypatch.setattr(wdb, "_get_workspace_db_path", lambda: dbp)
    with WorkspaceDBRepository.open(ensure_schema=True) as r:
        for i in range(3):
            r.upsert_entity("contact", f"c-{i}", f"Contact {i}", "workspace.db", "clients")
    return dbp


def _capture(handler, args, capsys):
    handler(args)
    import json

    return json.loads(capsys.readouterr().out)


# ── registered projections ──────────────────────────────────────────────


def test_entity_list_output_contract(seeded_workspace, capsys):
    payload = _capture(
        entity_commands.handle_entity_list_command,
        SimpleNamespace(type=None, status="active", limit=100, output="json"),
        capsys,
    )
    assert payload["ok"] is True
    assert_count_matches_list(payload, "count", "entities")
    assert len(payload["entities"]) == 3  # real data — not a trivially-empty pass
    assert_items_nonnull(payload["entities"], ["entity_type", "entity_id", "display_name"])


def test_entity_search_output_contract(seeded_workspace, capsys):
    payload = _capture(
        entity_commands.handle_entity_search_command,
        SimpleNamespace(query="Contact", type=None, status="active", limit=50, output="json"),
        capsys,
    )
    assert payload["ok"] is True
    assert_count_matches_list(payload, "count", "entities")
    assert_items_nonnull(payload["entities"], ["entity_type", "entity_id", "display_name"])


# ── the guard has teeth: violations must fail ───────────────────────────


def test_invariant_catches_count_list_divergence():
    with pytest.raises(AssertionError, match="diverging from its list"):
        assert_count_matches_list({"count": 5, "entities": [1, 2]}, "count", "entities")


def test_invariant_catches_masked_field():
    with pytest.raises(AssertionError, match="silently-masked field"):
        assert_items_nonnull([{"entity_id": "c-1", "display_name": None}], ["display_name"])

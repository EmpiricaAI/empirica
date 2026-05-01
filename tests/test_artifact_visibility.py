"""Phase 0 tests for the artifact visibility primitive.

Covers:
1. normalize_visibility — default behavior + override semantics
2. Repository methods persist visibility on every artifact type
3. Default fallback when --visibility is omitted
4. log-artifacts batch handler reads visibility per node
5. CLI visibility list/show output schema

See docs/architecture/PROPOSAL_VISIBILITY_TIERS.md.
"""

from __future__ import annotations

import sqlite3
import uuid

import pytest

from empirica.data.visibility import (
    DEFAULT_VISIBILITY,
    VISIBILITY_TIERS,
    normalize_visibility,
)


@pytest.fixture
def fresh_db(tmp_path):
    """Create a fresh SessionDatabase with a temp SQLite file."""
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "test_visibility.db"))
    yield db
    db.close()


PROJECT_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())


# ── normalize_visibility ────────────────────────────────────────────────────


class TestNormalize:
    def test_default_when_none(self):
        assert normalize_visibility(None) == DEFAULT_VISIBILITY
        assert DEFAULT_VISIBILITY == 'shared'

    def test_each_valid_tier_passes_through(self):
        for tier in VISIBILITY_TIERS:
            assert normalize_visibility(tier) == tier

    def test_unknown_falls_back_to_default(self):
        assert normalize_visibility('top-secret') == DEFAULT_VISIBILITY
        assert normalize_visibility('') == DEFAULT_VISIBILITY

    def test_case_insensitive(self):
        assert normalize_visibility('PUBLIC') == 'public'
        assert normalize_visibility('  Local  ') == 'local'

    def test_safe_invariant_never_promotes_to_public(self):
        """An unrecognized tier must NOT silently become 'public'."""
        for bogus in (None, '', 'x', '???', 'pub', 'secret'):
            assert normalize_visibility(bogus) != 'public'


# ── Repository persistence ──────────────────────────────────────────────────


def _column_value(db, table: str, artifact_id: str, column: str = 'visibility'):
    cursor = db.conn.cursor()
    cursor.execute(f"SELECT {column} FROM {table} WHERE id = ?", (artifact_id,))
    row = cursor.fetchone()
    return row[0] if row else None


class TestRepositoryPersistence:
    def test_finding_default_is_shared(self, fresh_db):
        fid = fresh_db.log_finding(PROJECT_ID, SESSION_ID, "default tier finding")
        assert _column_value(fresh_db, 'project_findings', fid) == 'shared'

    @pytest.mark.parametrize("tier", VISIBILITY_TIERS)
    def test_finding_explicit_tier(self, fresh_db, tier):
        fid = fresh_db.log_finding(PROJECT_ID, SESSION_ID,
                                   f"explicit {tier} finding", visibility=tier)
        assert _column_value(fresh_db, 'project_findings', fid) == tier

    def test_unknown_default_is_shared(self, fresh_db):
        uid = fresh_db.log_unknown(PROJECT_ID, SESSION_ID, "default tier unknown")
        assert _column_value(fresh_db, 'project_unknowns', uid) == 'shared'

    @pytest.mark.parametrize("tier", VISIBILITY_TIERS)
    def test_unknown_explicit_tier(self, fresh_db, tier):
        uid = fresh_db.log_unknown(PROJECT_ID, SESSION_ID,
                                   f"explicit {tier} unknown", visibility=tier)
        assert _column_value(fresh_db, 'project_unknowns', uid) == tier

    @pytest.mark.parametrize("tier", VISIBILITY_TIERS)
    def test_dead_end_explicit_tier(self, fresh_db, tier):
        did = fresh_db.log_dead_end(PROJECT_ID, SESSION_ID,
                                    f"approach {tier}", "why_failed",
                                    visibility=tier)
        assert _column_value(fresh_db, 'project_dead_ends', did) == tier

    def test_dead_end_default_is_shared(self, fresh_db):
        did = fresh_db.log_dead_end(PROJECT_ID, SESSION_ID,
                                    "approach default", "why_failed")
        assert _column_value(fresh_db, 'project_dead_ends', did) == 'shared'

    @pytest.mark.parametrize("tier", VISIBILITY_TIERS)
    def test_mistake_explicit_tier(self, fresh_db, tier):
        mid = fresh_db.log_mistake(SESSION_ID,
                                   f"mistake {tier}", "why_wrong",
                                   project_id=PROJECT_ID, visibility=tier)
        assert _column_value(fresh_db, 'mistakes_made', mid) == tier

    def test_mistake_default_is_shared(self, fresh_db):
        mid = fresh_db.log_mistake(SESSION_ID,
                                   "mistake default", "why_wrong",
                                   project_id=PROJECT_ID)
        assert _column_value(fresh_db, 'mistakes_made', mid) == 'shared'

    @pytest.mark.parametrize("tier", VISIBILITY_TIERS)
    def test_assumption_explicit_tier(self, fresh_db, tier):
        aid = fresh_db.log_assumption(PROJECT_ID, SESSION_ID,
                                      f"assumption {tier}",
                                      visibility=tier)
        assert _column_value(fresh_db, 'assumptions', aid) == tier

    def test_assumption_default_is_shared(self, fresh_db):
        aid = fresh_db.log_assumption(PROJECT_ID, SESSION_ID,
                                      "assumption default")
        assert _column_value(fresh_db, 'assumptions', aid) == 'shared'

    @pytest.mark.parametrize("tier", VISIBILITY_TIERS)
    def test_decision_explicit_tier(self, fresh_db, tier):
        did = fresh_db.log_decision(PROJECT_ID, SESSION_ID,
                                    f"choice {tier}", "rationale",
                                    visibility=tier)
        assert _column_value(fresh_db, 'decisions', did) == tier

    def test_decision_default_is_shared(self, fresh_db):
        did = fresh_db.log_decision(PROJECT_ID, SESSION_ID,
                                    "choice default", "rationale")
        assert _column_value(fresh_db, 'decisions', did) == 'shared'

    def test_invalid_tier_falls_back_to_default(self, fresh_db):
        """Defense-in-depth: bad value at the repo layer becomes 'shared'."""
        fid = fresh_db.log_finding(PROJECT_ID, SESSION_ID,
                                   "bogus tier finding",
                                   visibility='top-secret')
        assert _column_value(fresh_db, 'project_findings', fid) == 'shared'


# ── log-artifacts batch handler ─────────────────────────────────────────────


class TestBatchHandler:
    def test_node_visibility_passed_through(self, fresh_db):
        """The graph batch should propagate per-node visibility into the row."""
        from empirica.cli.command_handlers.graph_commands import _create_node

        ctx = {
            'session_id': SESSION_ID,
            'project_id': PROJECT_ID,
            'goal_id': None,
            'transaction_id': None,
        }
        finding_node = {
            'ref': 'f1',
            'type': 'finding',
            'data': {'finding': 'batch public', 'visibility': 'public'},
        }
        decision_node = {
            'ref': 'd1',
            'type': 'decision',
            'data': {
                'choice': 'batch local choice',
                'rationale': 'because',
                'visibility': 'local',
            },
        }

        fid = _create_node(fresh_db, finding_node, ctx)
        did = _create_node(fresh_db, decision_node, ctx)

        assert _column_value(fresh_db, 'project_findings', fid) == 'public'
        assert _column_value(fresh_db, 'decisions', did) == 'local'

    def test_node_without_visibility_defaults_to_shared(self, fresh_db):
        from empirica.cli.command_handlers.graph_commands import _create_node

        ctx = {
            'session_id': SESSION_ID,
            'project_id': PROJECT_ID,
            'goal_id': None,
            'transaction_id': None,
        }
        node = {
            'ref': 'f1',
            'type': 'finding',
            'data': {'finding': 'batch default'},
        }
        fid = _create_node(fresh_db, node, ctx)
        assert _column_value(fresh_db, 'project_findings', fid) == 'shared'


# ── CLI visibility list/show ────────────────────────────────────────────────


class TestVisibilityCli:
    """The list/show CLI handlers open their own SessionDatabase, so we
    monkey-patch the import inside visibility_commands to return a factory
    that yields a connection to the test database for each call."""

    @staticmethod
    def _patch_session_db(monkeypatch, db_path: str):
        from empirica.data.session_database import SessionDatabase

        def _factory():
            return SessionDatabase(db_path=db_path)

        monkeypatch.setattr(
            'empirica.cli.command_handlers.visibility_commands.SessionDatabase',
            _factory,
        )

    def test_list_aggregates_by_tier(self, fresh_db, capsys, monkeypatch):
        """`visibility list --output json` aggregates totals across types."""
        from empirica.cli.command_handlers.visibility_commands import (
            handle_visibility_list_command,
        )

        # Plant one finding per tier
        fresh_db.log_finding(PROJECT_ID, SESSION_ID, "list test public",
                             visibility='public')
        fresh_db.log_finding(PROJECT_ID, SESSION_ID, "list test shared",
                             visibility='shared')
        fresh_db.log_finding(PROJECT_ID, SESSION_ID, "list test local",
                             visibility='local')
        db_path = fresh_db.db_path
        self._patch_session_db(monkeypatch, db_path)

        class _Args:
            project_id = PROJECT_ID
            tier = None
            artifact_type = 'finding'
            limit = 5
            output = 'json'

        rc = handle_visibility_list_command(_Args())
        captured = capsys.readouterr()
        assert rc == 0
        import json as _json
        payload = _json.loads(captured.out)
        assert payload['ok'] is True
        finding_counts = payload['by_type']['finding']
        assert finding_counts['public'] == 1
        assert finding_counts['shared'] == 1
        assert finding_counts['local'] == 1

    def test_show_returns_tier_for_known_id(self, fresh_db, capsys, monkeypatch):
        from empirica.cli.command_handlers.visibility_commands import (
            handle_visibility_show_command,
        )

        fid = fresh_db.log_finding(PROJECT_ID, SESSION_ID,
                                   "show test public", visibility='public')
        db_path = fresh_db.db_path
        self._patch_session_db(monkeypatch, db_path)

        class _Args:
            artifact_id = fid
            output = 'json'

        rc = handle_visibility_show_command(_Args())
        captured = capsys.readouterr()
        assert rc == 0
        import json as _json
        payload = _json.loads(captured.out)
        assert payload['ok'] is True
        assert payload['type'] == 'finding'
        assert payload['visibility'] == 'public'

    def test_show_unknown_id_returns_error(self, fresh_db, capsys, monkeypatch):
        from empirica.cli.command_handlers.visibility_commands import (
            handle_visibility_show_command,
        )

        db_path = fresh_db.db_path
        self._patch_session_db(monkeypatch, db_path)

        class _Args:
            artifact_id = '00000000-aaaa-bbbb-cccc-000000000000'
            output = 'json'

        rc = handle_visibility_show_command(_Args())
        captured = capsys.readouterr()
        assert rc == 1
        import json as _json
        payload = _json.loads(captured.out)
        assert payload['ok'] is False


# ── Migration idempotency ───────────────────────────────────────────────────


class TestMigration:
    def test_visibility_column_exists_on_all_artifact_tables(self, fresh_db):
        """The migration should land the visibility column on every artifact table."""
        cursor = fresh_db.conn.cursor()
        for table in ('project_findings', 'project_unknowns', 'project_dead_ends',
                      'mistakes_made', 'assumptions', 'decisions', 'goals'):
            cursor.execute("SELECT 1 FROM pragma_table_info(?) WHERE name = 'visibility'",
                           (table,))
            assert cursor.fetchone() is not None, f"{table}.visibility missing"

    def test_migration_runs_idempotently(self, tmp_path):
        """Running the migration a second time on the same DB must be a no-op."""
        from empirica.data.migrations.migrations import migration_039_artifact_visibility

        db_path = tmp_path / "idempotency.db"
        # First open creates schema + applies all migrations
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase(db_path=str(db_path))

        # Run migration_039 again on a raw connection — should not raise
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        migration_039_artifact_visibility(cursor)  # should be silent no-op
        conn.commit()
        conn.close()
        db.close()

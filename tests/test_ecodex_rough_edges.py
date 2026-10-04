"""Three rough edges ecodex reported from one session (prop_salhqpnumfhf7nu6jrwwcndk6i), as data.

1. `goals-activate` run between transactions linked the goal to the PREVIOUS, closed transaction, so the Sentinel saw no goal in
   the new one. The verb now links only to an open transaction and says when it did not.
2. The sqlite read classifier scanned the whole query for write keywords, string literals included, so a pure SELECT that
   mentions `update` in a LIKE pattern was gated as praxic. Keywords inside quoted literals are not statements.
3. `delete-artifacts` has no --project-id and runs against the project the instance is pinned to, not the cwd; its output named
   neither, so a preview of 0 rows read as the cwd's. The output now names the database and says when it is not the cwd's.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks"
LIB = HOOKS.parent / "lib"


@pytest.fixture(scope="module")
def gate():
    sys.path.insert(0, str(LIB))
    spec = importlib.util.spec_from_file_location("sentinel_gate_rough_edges", HOOKS / "sentinel-gate.py")
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass
    return mod


def _safe(gate, command: str) -> bool:
    return gate.is_safe_bash_command({"command": command})


# ── 2. string literals are not statements ───────────────────────────────────


@pytest.mark.parametrize("word", ["update", "insert", "delete", "drop", "create", "alter", "replace", "attach"])
def test_a_select_whose_literal_mentions_a_write_word_is_a_read(gate, word):
    assert _safe(gate, f"""sqlite3 x.db "SELECT id FROM t WHERE note LIKE '%ecodex {word}%'" """)


def test_the_reported_query_is_a_read(gate):
    assert _safe(
        gate, """sqlite3 .empirica/sessions/sessions.db "SELECT id FROM goals WHERE x LIKE '%ecodex update%'" """
    )


def test_a_doubled_quote_stays_inside_the_literal(gate):
    assert _safe(gate, """sqlite3 x.db "SELECT 'it''s an update' AS s" """)


def test_a_double_quoted_identifier_is_not_a_statement(gate):
    assert _safe(gate, """sqlite3 x.db 'SELECT "update" FROM t' """)


# negative controls: every one of these must STILL gate, or the scan was loosened rather than corrected
@pytest.mark.parametrize(
    "query",
    [
        "UPDATE t SET x = 1",
        "SELECT 1; UPDATE t SET x = 1",
        "SELECT 'a'; DROP TABLE t",
        "SELECT '' ; DELETE FROM t",
        "SELECT 'x'';'; DELETE FROM t",  # the ';' is inside the literal, the DELETE after it is not
        "WITH c AS (SELECT 1) DELETE FROM t",
        "SELECT 'unterminated; DELETE FROM t",  # an unterminated literal is scanned as code, not trusted
        "SELECT 1 /* comment */; INSERT INTO t VALUES (1)",
    ],
)
def test_a_real_write_still_gates(gate, query):
    assert not _safe(gate, f'sqlite3 x.db "{query}"')


def test_a_write_hidden_after_a_literal_that_mentions_select_still_gates(gate):
    assert not _safe(gate, """sqlite3 x.db "SELECT 'select update' ; UPDATE t SET x = 1" """)


# ── 1. goals-activate between transactions ──────────────────────────────────


def _activate(monkeypatch, capsys, tx_state):
    import empirica.cli.command_handlers.goal_commands as gcmd

    seen = {}

    class _Repo:
        def __init__(self, _conn):
            pass

        def activate_goal(self, goal_id, transaction_id=None):
            seen["transaction_id"] = transaction_id
            return True

    monkeypatch.setattr("empirica.data.repositories.goals.GoalDataRepository", _Repo)
    monkeypatch.setattr(
        "empirica.data.session_database.SessionDatabase",
        lambda *a, **k: types.SimpleNamespace(conn=object(), close=lambda: None),
    )
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.transaction_read", staticmethod(lambda *a, **k: tx_state)
    )
    gcmd.handle_goals_activate_command(types.SimpleNamespace(goal_id="g1", output="json"))
    return seen, json.loads(capsys.readouterr().out)


def test_activate_links_to_an_open_transaction(monkeypatch, capsys):
    seen, out = _activate(monkeypatch, capsys, {"transaction_id": "tx-open", "status": "open"})

    assert seen["transaction_id"] == "tx-open"
    assert out["transaction_id"] == "tx-open" and out["transaction_linked"] is True and "warning" not in out


def test_activate_does_not_link_to_a_closed_transaction_and_says_so(monkeypatch, capsys):
    seen, out = _activate(monkeypatch, capsys, {"transaction_id": "tx-closed", "status": "closed"})

    assert seen["transaction_id"] is None, "the closed transaction is not this work's"
    assert out["ok"] is True and out["transaction_linked"] is False
    assert "PREFLIGHT" in out["warning"]


def test_activate_with_no_transaction_file_warns_the_same_way(monkeypatch, capsys):
    seen, out = _activate(monkeypatch, capsys, None)

    assert seen["transaction_id"] is None and out["transaction_linked"] is False and "PREFLIGHT" in out["warning"]


# ── 3. delete-artifacts names what it ran against ───────────────────────────


def _delete(tmp_path, monkeypatch, capsys, cwd_root):
    from empirica.cli.command_handlers.graph_commands import handle_delete_artifacts_command
    from empirica.data.session_database import SessionDatabase

    dbp = tmp_path / "pinned" / ".empirica" / "sessions" / "sessions.db"
    dbp.parent.mkdir(parents=True)
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(dbp))
    SessionDatabase(db_path=str(dbp)).close()
    monkeypatch.setattr("empirica.config.path_resolver.get_git_root", lambda: cwd_root)
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"prune_dangling": True, "reason": "t"}))
    handle_delete_artifacts_command(types.SimpleNamespace(config=str(cfg), apply=False, schema=False))
    return json.loads(capsys.readouterr().out), dbp


def test_the_output_names_the_database_it_ran_against(tmp_path, monkeypatch, capsys):
    out, dbp = _delete(tmp_path, monkeypatch, capsys, tmp_path / "pinned")

    assert out["target"]["db_path"] == str(dbp)
    assert out["target"]["matches_cwd"] is True and "warning" not in out


def test_a_database_that_is_not_the_cwds_is_flagged(tmp_path, monkeypatch, capsys):
    """The ecodex case: a loop over practice directories, every run pinned to the same practice."""
    out, _ = _delete(tmp_path, monkeypatch, capsys, tmp_path / "somewhere-else")

    assert out["target"]["matches_cwd"] is False
    assert "not the current directory's" in out["warning"] and "project" in out["warning"]


# ── 4. plainly read-only commands the classifier gated (ecodex follow-up, prop_qioc34jtwjhonn5xts2lei63ee) ──────────────────


@pytest.mark.parametrize(
    "command",
    [
        "readlink -f /tmp",
        "realpath /tmp",
        "git merge-base --is-ancestor HEAD~1 HEAD",
        "git merge-base HEAD origin/develop",
        "claude --version",
        "empirica --version; readlink -f /tmp; git log -1; rg -n foo file",
        "git log -1 && git merge-base --is-ancestor a b",
    ],
)
def test_a_plain_read_is_not_gated(gate, command):
    assert _safe(gate, command)


@pytest.mark.parametrize(
    "command",
    [
        "readlink -f /tmp > out.txt",
        "realpath /tmp; rm -rf x",
        "git merge-base HEAD x && git push",
        "claude --version; touch x",
        "claude login",
        "claude --dangerously-skip-permissions",
    ],
)
def test_the_new_reads_do_not_open_a_write_path(gate, command):
    """Control: each of these is a real write, a chain into one, or a different claude verb, and must still gate."""
    assert not _safe(gate, command)


def test_infra_only_inspection_stays_gated_outside_infra_work(gate):
    """`ss`, `free` and `uptime` are reads, but they are allowed by work_type on purpose (INFRA_SAFE_PREFIXES), not globally."""
    for command in ("ss -ltn", "free -m", "uptime"):
        assert not _safe(gate, command), command

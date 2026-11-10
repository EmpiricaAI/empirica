"""An INVESTIGATE from an earlier CHECK asks the person once, and not after its transaction closed (2026-11-10).

`_check_prior_investigate` took the session's latest CHECK and, when it said investigate and came from another transaction,
returned "ask" on EVERY praxic call until a finding was logged. An "ask" is a permission prompt, so David was asked to
confirm every run of the next transaction, though the investigating one had been closed with POSTFLIGHT. Its docstring
already promised a one-time ask per transaction.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"


@pytest.fixture
def gate(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    spec = importlib.util.spec_from_file_location("sentinel_gate_prior_investigate", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "_instance_suffix", lambda: "_t")
    return mod


def _cursor(*, closed: bool):
    db = sqlite3.connect(":memory:")
    db.executescript(
        "CREATE TABLE reflexes (session_id TEXT, phase TEXT, reflex_data TEXT, transaction_id TEXT, timestamp REAL);"
        "CREATE TABLE project_findings (session_id TEXT, created_timestamp REAL);"
    )
    db.execute(
        "INSERT INTO reflexes VALUES ('s', 'CHECK', ?, 'tx-old', 10)", (json.dumps({"decision": "investigate"}),)
    )
    if closed:
        db.execute("INSERT INTO reflexes VALUES ('s', 'POSTFLIGHT', '{}', 'tx-old', 20)")
    return db.cursor()


EDIT = ("Edit", {"file_path": "/p/a.py", "old_string": "a", "new_string": "b"})


def test_an_investigate_from_a_closed_transaction_does_not_ask(gate):
    cur = _cursor(closed=True)
    assert gate._check_prior_investigate(cur, "s", "tx-new", 30, *EDIT) is None


def test_an_open_investigate_asks_once_per_transaction_not_on_every_call(gate):
    cur = _cursor(closed=False)
    first = gate._check_prior_investigate(cur, "s", "tx-new", 30, *EDIT)
    second = gate._check_prior_investigate(cur, "s", "tx-new", 30, *EDIT)
    assert first is not None and first[0] == "ask"
    assert second is None, "the person was already asked in this transaction"
    again = gate._check_prior_investigate(cur, "s", "tx-next", 30, *EDIT)
    assert again is not None and again[0] == "ask", "a later transaction is asked once more while it stays open"


def test_a_finding_in_the_current_transaction_still_satisfies_it(gate):
    cur = _cursor(closed=False)
    cur.execute("INSERT INTO project_findings VALUES ('s', 40)")
    assert gate._check_prior_investigate(cur, "s", "tx-new", 30, *EDIT) is None

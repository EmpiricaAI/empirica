"""local means kept off Cortex (David 2026-11-19); the 1.17.12 sweep found two more paths that did not hold it."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_sources_reconcile_leaves_local_sources_out():
    """Their hashes, ids and local paths went to /v1/sources/catalogue and /reconcile, and --apply uploaded small bodies."""
    from types import SimpleNamespace

    from empirica.cli.command_handlers.sources_reconcile_commands import _load_local_sources

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE epistemic_sources (id TEXT, project_id TEXT, title TEXT, source_url TEXT, content_hash TEXT, "
        "size_bytes INT, canonical_path TEXT, mime_type TEXT, source_metadata TEXT, archived INT, visibility TEXT)"
    )
    for sid, vis in (("s-local", "local"), ("s-null", None), ("s-shared", "shared"), ("s-public", "public")):
        conn.execute(
            "INSERT INTO epistemic_sources (id, project_id, title, visibility) VALUES (?, 'p', 't', ?)", (sid, vis)
        )
    rows = _load_local_sources(SimpleNamespace(conn=conn), "p")
    assert {r["id"] for r in rows} == {"s-shared", "s-public"}


def test_an_untagged_row_reads_as_local_wherever_core_decides_what_reaches_cortex():
    """POSTFLIGHT's sync read a NULL visibility as shared and sent it; mesh connect read it as local. Fail closed: local."""
    offenders = []
    for path in (_ROOT / "empirica").rglob("*.py"):
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"COALESCE\(\s*visibility\s*,\s*'shared'\s*\)", line, re.IGNORECASE):
                offenders.append(f"{path.relative_to(_ROOT)}:{n}")
    assert offenders == []


def test_the_deny_lists_count_an_untagged_row_as_local():
    """1.17.12 second pass: session-init's and POSTFLIGHT's local deny-lists matched visibility = 'local' only."""
    offenders = []
    for path in [*(_ROOT / "empirica").rglob("*.py")]:
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"WHERE id IN \(\{.*\}\) AND visibility = 'local'", line):
                offenders.append(f"{path.relative_to(_ROOT)}:{n}")
    assert offenders == []


def test_sources_reconcile_says_how_many_local_sources_it_left_out():
    from types import SimpleNamespace

    from empirica.cli.command_handlers.sources_reconcile_commands import _count_local_sources

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE epistemic_sources (id TEXT, project_id TEXT, archived INT, visibility TEXT)")
    conn.executemany(
        "INSERT INTO epistemic_sources VALUES (?, 'p', 0, ?)", [("a", "local"), ("b", None), ("c", "shared")]
    )
    assert _count_local_sources(SimpleNamespace(conn=conn), "p") == 2

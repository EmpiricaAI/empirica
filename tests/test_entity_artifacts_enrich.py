"""Tests for _enrich_source_artifacts — join entity-artifact source edges to
their content so the knowledge pane renders titles/descriptions, not opaque
UUIDs (workspace prop_tu3o343). Best-effort, read-only, cross-DB by
``artifact_source`` (the source's home ``.empirica`` dir).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from empirica.api.routes.entities import _enrich_source_artifacts


def _make_source_db(empirica_dir: Path, sources: list[tuple]) -> None:
    """sources rows = (id, title, source_url, canonical_path, description, source_type)."""
    db_dir = empirica_dir / "sessions"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "sessions.db"))
    conn.execute(
        "CREATE TABLE epistemic_sources (id TEXT PRIMARY KEY, title TEXT, source_url TEXT, "
        "canonical_path TEXT, description TEXT, source_type TEXT)"
    )
    conn.executemany("INSERT INTO epistemic_sources VALUES (?,?,?,?,?,?)", sources)
    conn.commit()
    conn.close()


def test_enriches_source_row(tmp_path):
    ed = tmp_path / "proj" / ".empirica"
    _make_source_db(ed, [("s1", "CEO brain download", None, "/docs/ceo.md", "the curated skill", "document")])
    arts = [{"artifact_type": "source", "artifact_id": "s1", "artifact_source": str(ed)}]
    _enrich_source_artifacts(arts)
    assert arts[0]["title"] == "CEO brain download"
    assert arts[0]["path"] == "/docs/ceo.md"
    assert arts[0]["description"] == "the curated skill"
    assert arts[0]["source_type"] == "document"


def test_path_falls_back_to_url(tmp_path):
    ed = tmp_path / "p" / ".empirica"
    _make_source_db(ed, [("s2", "RFC 7519", "https://x/rfc", None, None, "url")])
    arts = [{"artifact_type": "source", "artifact_id": "s2", "artifact_source": str(ed)}]
    _enrich_source_artifacts(arts)
    assert arts[0]["path"] == "https://x/rfc"


def test_cross_db_resolution(tmp_path):
    ed1 = tmp_path / "a" / ".empirica"
    _make_source_db(ed1, [("x", "A-title", None, "/a", None, "doc")])
    ed2 = tmp_path / "b" / ".empirica"
    _make_source_db(ed2, [("y", "B-title", None, "/b", None, "doc")])
    arts = [
        {"artifact_type": "source", "artifact_id": "x", "artifact_source": str(ed1)},
        {"artifact_type": "source", "artifact_id": "y", "artifact_source": str(ed2)},
    ]
    _enrich_source_artifacts(arts)
    assert arts[0]["title"] == "A-title"
    assert arts[1]["title"] == "B-title"


def test_missing_db_leaves_pointer(tmp_path):
    arts = [{"artifact_type": "source", "artifact_id": "s1", "artifact_source": str(tmp_path / "gone" / ".empirica")}]
    _enrich_source_artifacts(arts)
    assert "title" not in arts[0]  # unchanged — best-effort


def test_non_source_type_untouched(tmp_path):
    ed = tmp_path / "p" / ".empirica"
    _make_source_db(ed, [("s1", "T", None, "/p", None, "doc")])
    arts = [{"artifact_type": "goal", "artifact_id": "g1", "artifact_source": str(ed)}]
    _enrich_source_artifacts(arts)
    assert "title" not in arts[0]


def test_no_epistemic_sources_table_is_best_effort(tmp_path):
    ed = tmp_path / "p" / ".empirica"
    db_dir = ed / "sessions"
    db_dir.mkdir(parents=True)
    conn = sqlite3.connect(str(db_dir / "sessions.db"))
    conn.execute("CREATE TABLE other (x TEXT)")
    conn.commit()
    conn.close()
    arts = [{"artifact_type": "source", "artifact_id": "s1", "artifact_source": str(ed)}]
    _enrich_source_artifacts(arts)
    assert "title" not in arts[0]  # OperationalError swallowed, row untouched

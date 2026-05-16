"""Regression tests for stray .empirica/ dirs in subdirs.

Original report (2026-05-16): running `empirica project-bootstrap` from
`<project>/docs/` created `<project>/docs/.empirica/cache/dep_graph_docs.json`
because bootstrap_project_breadcrumbs defaulted project_root to os.getcwd().
Subsequent CLI invocations then resolved the stray as a valid project root
and tried to open a non-existent sessions.db ("unable to open database file").

Two-sided fix:
  - Writer: bootstrap default no longer trusts CWD; _generate_dependency_summary
    refuses to mkdir under a path that lacks a marker file.
  - Resolver: is_project_root() requires .empirica/project.yaml OR
    .empirica/sessions/sessions.db, not bare .empirica/ existence.
"""

from __future__ import annotations

from pathlib import Path

from empirica.utils.session_resolver import is_project_root

# ── is_project_root predicate ────────────────────────────────────────────


def test_is_project_root_true_with_project_yaml(tmp_path: Path):
    proj = tmp_path / "p"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("name: p\n")
    assert is_project_root(proj) is True


def test_is_project_root_true_with_sessions_db(tmp_path: Path):
    proj = tmp_path / "p"
    (proj / ".empirica" / "sessions").mkdir(parents=True)
    (proj / ".empirica" / "sessions" / "sessions.db").write_text("")
    assert is_project_root(proj) is True


def test_is_project_root_false_with_cache_only_stray(tmp_path: Path):
    """The exact symptom from the original report — cache-only .empirica/."""
    stray = tmp_path / "docs"
    (stray / ".empirica" / "cache").mkdir(parents=True)
    (stray / ".empirica" / "cache" / "dep_graph_docs.json").write_text("{}")
    assert is_project_root(stray) is False


def test_is_project_root_false_with_no_empirica(tmp_path: Path):
    assert is_project_root(tmp_path) is False


def test_is_project_root_accepts_str_or_path(tmp_path: Path):
    proj = tmp_path / "p"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("name: p\n")
    assert is_project_root(str(proj)) is True
    assert is_project_root(proj) is True


# ── _generate_dependency_summary mkdir guard ─────────────────────────────


def test_dep_summary_does_not_create_empirica_under_subdir(tmp_path: Path):
    """Writer-side belt-and-suspenders: refuse to mkdir under non-project path.

    Even if a future caller passes project_root pointing at an arbitrary
    subdir, no stray .empirica/cache/ may be created there.
    """
    from empirica.data.session_database import SessionDatabase

    # Real project so SessionDatabase init can succeed
    real_proj = tmp_path / "real"
    (real_proj / ".empirica" / "sessions").mkdir(parents=True)
    (real_proj / ".empirica" / "project.yaml").write_text("name: real\n")
    db_path = real_proj / ".empirica" / "sessions" / "sessions.db"
    db_path.write_text("")  # empty file — adapter will init schema

    db = SessionDatabase(str(db_path))
    try:
        # Non-project subdir — a docs/ folder with no .empirica/
        subdir = real_proj / "docs"
        subdir.mkdir()

        result = db._generate_dependency_summary(str(subdir))

        # Returns None (refused) rather than walking and writing cache
        assert result is None
        # CRITICAL: no stray .empirica/ created under subdir
        assert not (subdir / ".empirica").exists(), \
            "Writer created stray .empirica/ under non-project subdir"
    finally:
        db.close()


def test_dep_summary_works_for_real_project_root(tmp_path: Path):
    """Sanity: the guard doesn't break the legitimate use case."""
    from empirica.data.session_database import SessionDatabase

    proj = tmp_path / "real"
    (proj / ".empirica" / "sessions").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("name: real\n")
    db_path = proj / ".empirica" / "sessions" / "sessions.db"
    db_path.write_text("")

    # Add a python file so the walker has something to find
    (proj / "main.py").write_text("import os\n")

    db = SessionDatabase(str(db_path))
    try:
        result = db._generate_dependency_summary(str(proj))
        assert result is not None
        assert "module_count" in result
        assert result["module_count"] >= 1
        # Cache file gets written under the real project (expected)
        assert (proj / ".empirica" / "cache" / "dep_graph_real.json").exists()
    finally:
        db.close()


# ── _resolve_canonical_project_root never falls back to CWD ──────────────


def test_canonical_resolver_returns_none_when_unresolvable(
    tmp_path: Path, monkeypatch
):
    """Even with CWD inside a non-project subdir, never return CWD."""
    from empirica.data.session_database import _resolve_canonical_project_root

    # CWD is a real directory but not a project
    subdir = tmp_path / "random_subdir"
    subdir.mkdir()
    monkeypatch.chdir(subdir)

    # Stub the two resolvers to return None (simulating "no context found")
    monkeypatch.setattr(
        "empirica.utils.session_resolver.InstanceResolver.project_path",
        staticmethod(lambda *a, **k: None),
    )
    monkeypatch.setattr(
        "empirica.utils.session_resolver.find_project_root",
        lambda *a, **k: None,
    )

    result = _resolve_canonical_project_root()
    assert result is None, (
        "Resolver fell back to CWD instead of returning None — "
        "this is the bug pattern that creates stray .empirica/ dirs"
    )

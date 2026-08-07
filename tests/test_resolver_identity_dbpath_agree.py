"""Project IDENTITY and the sessions.db PATH must not disagree.

Two resolvers answer "which project am I in", with different guards:

  session_resolver._cwd_project_override   identity   harness-agnostic
  path_resolver._try_context_project_db    db path    gated on EMPIRICA_CWD_RELIABLE

The identity resolver keys on a harness-agnostic ground truth — cwd is a
registered project root — and self-heals. The db-path resolver gates its
cross-project bleed correction on ``EMPIRICA_CWD_RELIABLE``, a variable set once
by a SessionStart hook into a process tree that Claude Code's per-call Bash
subprocesses are not in. It therefore never arrives for CLI invocations, the
correction never runs, and the db path stays pinned to the stale project.

Observed by empirica.david.ecodex: the stale-mapping warning printed (the
identity half announcing that it trusted cwd) while ``goals-complete`` returned
"Goal not found" for an existing goal and ``goals-list --all-projects --status
all --limit 500`` returned total=0 against a project holding 700+ findings.
PREFLIGHT/CHECK/POSTFLIGHT were correct in the same session because they read
identity; the goals verbs open the db.

The warning is emitted by the half that heals. The data comes from the half that
does not.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _make_project(root: Path) -> Path:
    """A registered project root with a real sessions.db."""
    (root / ".empirica").mkdir(parents=True, exist_ok=True)
    (root / ".empirica" / "project.yaml").write_text(f"ai_id: {root.name}\nname: {root.name}\n")
    db = root / ".empirica" / "sessions" / "sessions.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"")
    return db


@pytest.fixture
def two_projects(tmp_path, monkeypatch):
    """cwd is project B; the stale context still points at project A."""
    stale = tmp_path / "stale-project"
    here = tmp_path / "here-project"
    stale_db = _make_project(stale)
    here_db = _make_project(here)
    monkeypatch.chdir(here)
    return {"stale": stale, "here": here, "stale_db": stale_db, "here_db": here_db}


# ─── The identity half (already correct — pinned so it stays that way) ──


def test_identity_resolver_prefers_cwd_over_a_stale_mapping(two_projects):
    from empirica.utils.session_resolver import _cwd_project_override

    got = _cwd_project_override(str(two_projects["stale"]))
    assert got is not None, "identity must self-heal when cwd is a registered project root"
    assert Path(got).resolve() == two_projects["here"].resolve()


def test_identity_resolver_does_not_need_the_env_var(two_projects, monkeypatch):
    """This is the asymmetry: identity works with the var absent."""
    monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
    from empirica.utils.session_resolver import _cwd_project_override

    assert _cwd_project_override(str(two_projects["stale"])) is not None


# ─── The db-path half (the defect) ─────────────────────────────────────


def test_dbpath_resolver_also_heals_without_the_env_var(two_projects, monkeypatch):
    """NEGATIVE CONTROL — the reported bug, reduced.

    With ``EMPIRICA_CWD_RELIABLE`` absent (the state of every Claude Code Bash
    invocation), the db-path resolver must still refuse the stale project when
    cwd is itself a registered project root with its own database. Before the
    fix this returned the STALE project's db, which is why goals-list reported
    zero against a populated project.
    """
    monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
    from empirica.config.path_resolver import _try_context_project_db

    got = _try_context_project_db(str(two_projects["stale"]), two_projects["here"])
    assert got != two_projects["stale_db"], (
        "db path stayed on the stale project while identity resolved to cwd — "
        "the split that made the stale-mapping warning misleading"
    )


def test_dbpath_and_identity_agree_without_the_env_var(two_projects, monkeypatch):
    """The invariant, stated directly: one project, not two answers."""
    monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
    from empirica.config.path_resolver import _try_context_project_db
    from empirica.utils.session_resolver import _cwd_project_override

    identity = _cwd_project_override(str(two_projects["stale"]))
    db = _try_context_project_db(str(two_projects["stale"]), two_projects["here"])

    assert identity is not None
    assert db is not None, "db-path resolution returned nothing while identity resolved"
    # The db must sit under the project identity resolved to.
    assert Path(db).is_relative_to(Path(identity).resolve()) or Path(db).is_relative_to(Path(identity))


def test_env_var_still_honoured_when_it_is_present(two_projects, monkeypatch):
    """The var was never wrong, only unreachable — keep its behaviour intact."""
    monkeypatch.setenv("EMPIRICA_CWD_RELIABLE", "true")
    from empirica.config.path_resolver import _try_context_project_db

    got = _try_context_project_db(str(two_projects["stale"]), two_projects["here"])
    assert got != two_projects["stale_db"]


# ─── Guard rails: do not over-correct ──────────────────────────────────


def test_context_is_kept_when_cwd_is_not_a_registered_project(tmp_path, monkeypatch):
    """cwd being some random directory is NOT evidence the context is stale.

    Only a cwd that is itself a registered project root carries the ground truth.
    Without this the resolver would abandon a perfectly good context every time a
    command ran from a subdirectory or an unrelated path.
    """
    monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
    context = tmp_path / "context-project"
    context_db = _make_project(context)
    elsewhere = tmp_path / "just-a-folder"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    from empirica.config.path_resolver import _try_context_project_db

    assert _try_context_project_db(str(context), None) == context_db


def test_context_is_kept_when_cwd_project_has_no_database(tmp_path, monkeypatch):
    """A registered cwd project with no sessions.db cannot be preferred —
    switching to it would trade correct data for none."""
    monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
    context = tmp_path / "context-project"
    context_db = _make_project(context)
    bare = tmp_path / "bare-project"
    (bare / ".empirica").mkdir(parents=True)
    (bare / ".empirica" / "project.yaml").write_text("ai_id: bare-project\n")
    monkeypatch.chdir(bare)

    from empirica.config.path_resolver import _try_context_project_db

    assert _try_context_project_db(str(context), bare) == context_db


def test_same_project_is_left_alone(tmp_path, monkeypatch):
    """No divergence, nothing to correct."""
    monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
    proj = tmp_path / "only-project"
    db = _make_project(proj)
    monkeypatch.chdir(proj)

    from empirica.config.path_resolver import _try_context_project_db

    assert _try_context_project_db(str(proj), proj) == db


# ─── The premise that hid this ─────────────────────────────────────────


def test_identity_resolver_docstring_does_not_claim_claude_code_is_exempt():
    """The comment said Claude Code never reaches this guard because it sets
    EMPIRICA_CWD_RELIABLE. It does set it — into a process tree the Bash tool's
    per-call subprocesses are not in. Claude Code is the harness hitting the
    guard MOST, and that false premise is why nothing suspected it."""
    import inspect

    from empirica.utils import session_resolver

    doc = inspect.getdoc(session_resolver._cwd_project_override) or ""
    assert "Claude Code is unaffected" not in doc, (
        "the docstring still claims Claude Code is exempt — falsified by the permanent warning spam it produces"
    )


def test_env_var_is_not_the_sole_guard_in_path_resolver():
    """Structural: the db-path resolver must consult something other than an env
    var that cannot reach a subprocess."""
    import inspect

    from empirica.config import path_resolver

    src = inspect.getsource(path_resolver._try_context_project_db)
    assert "EMPIRICA_CWD_RELIABLE" in src, "guard removed entirely — it is still valid when present"
    assert "project.yaml" in src, (
        "db-path resolution has no harness-agnostic ground truth — it still depends "
        "solely on an env var that never arrives via the Bash tool"
    )

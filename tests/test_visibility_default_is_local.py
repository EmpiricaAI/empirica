"""Sharing is an opt-in act; a default cannot perform it.

Doctrine has always said "local (default) — stays in this project only". The
shipped code said `DEFAULT_VISIBILITY = "shared"`, and every unflagged artifact
funnelled through that constant — measured on David's stores alone: core 4,289
shared + 77 public, autonomy 553 shared, workspace (CRM rows!) 389 shared + 34
public, overwhelmingly defaulted rather than deliberate. Under an active L2
agreement, 'shared' IS cross-tenant exposure, so the old fallback's own
rationale ("never accidentally promote to public") guarded the wrong boundary.

Fix scope, deliberately narrow: the DEFAULT only. Historical rows are David's
pending ruling, and a mass change here would conflate the defect fix with the
adjudication. (Autonomy prop_7ptj5rbnybgmbejrhr5idbzbdy, core half.)
"""

from __future__ import annotations

from empirica.data.visibility import DEFAULT_VISIBILITY, normalize_visibility


def test_the_default_is_local():
    assert DEFAULT_VISIBILITY == "local"


def test_none_falls_closed():
    assert normalize_visibility(None) == "local"


def test_unknown_values_fall_closed_not_sideways():
    """An unrecognised tier must not resolve to MORE exposure than none at all."""
    assert normalize_visibility("everyone") == "local"
    assert normalize_visibility("") == "local"


def test_explicit_tiers_still_win():
    """The fix changes the FALLBACK, never an explicit choice."""
    assert normalize_visibility("shared") == "shared"
    assert normalize_visibility("public") == "public"
    assert normalize_visibility("LOCAL") == "local"


def test_schema_defaults_agree_with_the_constant():
    """Two-sources-of-truth guard: the SQL DEFAULT clauses must match
    DEFAULT_VISIBILITY, or direct inserts and CLI writes diverge silently —
    which is exactly how the doctrine/code split survived unnoticed."""
    import pathlib

    for path in (
        "empirica/data/schema/projects_schema.py",
        "empirica/data/repositories/workspace_db.py",
    ):
        src = pathlib.Path(path).read_text()
        assert "visibility TEXT DEFAULT 'shared'" not in src, f"{path} still defaults to shared"
        assert f"visibility TEXT DEFAULT '{DEFAULT_VISIBILITY}'" in src, (
            f"{path} schema default disagrees with DEFAULT_VISIBILITY"
        )


def test_help_strings_no_longer_advertise_shared_default():
    import pathlib

    for path in pathlib.Path("empirica/cli/parsers").glob("*.py"):
        assert "default: shared" not in path.read_text(), f"{path.name} still says default: shared"

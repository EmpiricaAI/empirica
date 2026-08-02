"""provision-practice must read the tenant/org spelling the rest of the repo uses.

`--tenant`/`--org` are documented to "default to whatever's already set in the
CURRENT directory's .empirica/project.yaml (so running this from inside an
existing practice provisions a sibling under the same tenant/org)".

That was false for every practice except one this command had provisioned
itself. It read the bare `tenant`/`org` keys; `setup` writes — and ~24 other
modules read — `tenant_slug`/`org_id`. Running `provision-practice --dry-run`
from inside this very repo, which carries `tenant_slug: david` and
`org_id: org-empirica`, failed with "couldn't be inferred".

Two spellings for one fact, with the reader and the writer on opposite sides:
the two-sources-of-truth class. The round-trip test at the bottom is the one
that would have caught it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from empirica.cli.command_handlers.provision_practice_commands import (  # noqa: E402
    _infer_tenant_org,
    _patch_project_yaml,
)


def _practice(tmp_path: Path, **fields) -> Path:
    """A practice directory whose project.yaml carries `fields`."""
    root = tmp_path / "practice"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(yaml.safe_dump(fields))
    return root


def test_infers_from_the_canonical_spelling(tmp_path):
    """THE BUG: this is what a real practice's project.yaml looks like."""
    root = _practice(tmp_path, name="X", tenant_slug="david", org_id="org-empirica")

    assert _infer_tenant_org(root) == ("david", "org-empirica")


def test_still_infers_from_the_legacy_spelling(tmp_path):
    """Practices this command provisioned before the fix must keep resolving."""
    root = _practice(tmp_path, name="X", tenant="david", org="org-empirica")

    assert _infer_tenant_org(root) == ("david", "org-empirica")


def test_canonical_wins_when_both_are_present(tmp_path):
    root = _practice(
        tmp_path,
        tenant_slug="canonical",
        org_id="org-canonical",
        tenant="legacy",
        org="org-legacy",
    )

    assert _infer_tenant_org(root) == ("canonical", "org-canonical")


@pytest.mark.parametrize("fields", [{}, {"name": "X"}, {"tenant_slug": "david"}])
def test_partial_or_absent_data_does_not_invent_a_value(tmp_path, fields):
    """A missing org must stay None, not become an empty string that passes a check."""
    root = _practice(tmp_path, **fields)
    tenant, org = _infer_tenant_org(root)

    assert org is None
    if "tenant_slug" not in fields:
        assert tenant is None


def test_no_project_yaml_is_not_an_error(tmp_path):
    (tmp_path / "bare").mkdir()
    assert _infer_tenant_org(tmp_path / "bare") == (None, None)


def test_unreadable_yaml_is_not_an_error(tmp_path):
    root = tmp_path / "practice"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text("{[not: valid: yaml")

    assert _infer_tenant_org(root) == (None, None)


def test_patch_writes_the_canonical_spelling(tmp_path):
    """Writing bare tenant/org is what made this command the repo's sole outlier.

    It minted practices whose tenant/org were invisible to every module that
    reads tenant_slug/org_id — including its own sibling inference.
    """
    root = _practice(tmp_path, name="X")
    project_yaml = root / ".empirica" / "project.yaml"

    _patch_project_yaml(project_yaml, "new-practice", "david", "org-empirica", "cortex", dry_run=False)
    data = yaml.safe_load(project_yaml.read_text())

    assert data["tenant_slug"] == "david"
    assert data["org_id"] == "org-empirica"
    assert data["ai_id"] == "new-practice"
    assert data["substrate"] == "cortex"
    assert "tenant" not in data and "org" not in data, "the outlier spelling must not be reintroduced"


def test_patch_preserves_unrelated_fields(tmp_path):
    root = _practice(tmp_path, name="X", project_id="abc-123", languages=["python"])
    project_yaml = root / ".empirica" / "project.yaml"

    _patch_project_yaml(project_yaml, "p", "david", "org-empirica", "cortex", dry_run=False)
    data = yaml.safe_load(project_yaml.read_text())

    assert data["project_id"] == "abc-123"
    assert data["languages"] == ["python"]


def test_dry_run_writes_nothing(tmp_path):
    root = _practice(tmp_path, name="X")
    project_yaml = root / ".empirica" / "project.yaml"
    before = project_yaml.read_text()

    changed = _patch_project_yaml(project_yaml, "p", "david", "org-empirica", "cortex", dry_run=True)

    assert changed is True, "dry-run still reports that a change WOULD happen"
    assert project_yaml.read_text() == before


def test_patch_then_infer_round_trips(tmp_path):
    """THE REGRESSION GUARD.

    The writer and the reader disagreed about the key names, so a practice this
    command created could not be used to provision its own sibling — the exact
    workflow the --tenant/--org defaults exist for. Asserting the round-trip
    makes any future divergence between the two impossible to ship.
    """
    root = _practice(tmp_path, name="X")

    _patch_project_yaml(root / ".empirica" / "project.yaml", "first", "david", "org-empirica", "cortex", dry_run=False)

    assert _infer_tenant_org(root) == ("david", "org-empirica")


def test_this_repos_own_project_yaml_resolves():
    """The literal case that failed: a real practice, standing in it.

    Skipped rather than failed where the file is absent (fresh clone, CI without
    a provisioned .empirica), because its absence is not a defect in this code.
    """
    root = Path(__file__).resolve().parent.parent
    if not (root / ".empirica" / "project.yaml").exists():
        pytest.skip("no local .empirica/project.yaml")

    tenant, org = _infer_tenant_org(root)

    assert tenant, "running provision-practice from inside a real practice must infer a tenant"
    assert org, "...and an org"

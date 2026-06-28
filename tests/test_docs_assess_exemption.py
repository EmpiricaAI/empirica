"""docs-assess proprietary-surface exemption (T9, goal bf8bb5e1).

The Cortex*/Ntfy* credential models in ``serve_app.py`` are PROPRIETARY
integration API surfaces (cortex credential exchange + the ntfy push transport).
docs-assess must EXEMPT them from the public-doc coverage metric (via
``[tool.empirica.docs-assess] ignore_classes`` in pyproject.toml) so that passing
coverage never requires publicly documenting the proprietary integration surface
— David's lane rule: keep the proprietary mesh/integration surface out of public
docs.
"""

from __future__ import annotations

from pathlib import Path

from empirica.cli.command_handlers import docs_commands as dc

PROPRIETARY = [
    "CortexCredentialsRequest",
    "CortexCredentialsResponse",
    "NtfyCredentialsRequest",
    "NtfyCredentialsResponse",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_proprietary_surfaces_exempt_in_repo_config():
    # Live regression guard: the shipped pyproject must keep these exempt. If a
    # future edit drops one, this fails loud (proprietary surface re-exposed to
    # the public-doc coverage requirement).
    cfg = dc._auto_detect_project_config(_repo_root())
    for name in PROPRIETARY:
        assert name in cfg.docs_ignore_classes, (
            f"{name} dropped from docs-assess ignore_classes — proprietary surface re-exposed"
        )


def test_exempt_surfaces_excluded_from_surface_count():
    # End-to-end: the exempt classes must NOT appear among the documentable
    # surfaces docs-assess counts, while a normal public model still does.
    agent = dc.EpistemicDocsAgent(project_root=_repo_root())
    modules, _ = agent._extract_core_modules()
    for name in PROPRIETARY:
        assert name not in modules, f"{name} counted as a documentable surface despite exemption"
    assert "EntityCreateRequest" in modules  # sanity — a real public API model is still counted


def test_is_class_ignored_honors_exemption():
    agent = dc.EpistemicDocsAgent(project_root=_repo_root())
    assert agent._is_class_ignored("CortexCredentialsRequest") is True
    assert agent._is_class_ignored("NtfyCredentialsResponse") is True
    assert agent._is_class_ignored("EntityCreateRequest") is False

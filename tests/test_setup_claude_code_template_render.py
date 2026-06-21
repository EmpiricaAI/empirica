"""Tests for versioned template rendering in setup-claude-code.

Closes Philipp's #100 — system-prompt template hardcoded v1.7.0, drifted
8 minor versions before being caught. Fix: parameterize via
{{ empirica_version }} and {{ generated_date }} placeholders, substitute
at write-time from the installed package.

These tests lock in the substitution so the bug can't recur.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from empirica.cli.command_handlers.setup_claude_code import (
    _render_versioned_template,
    _resolve_empirica_version,
)

# ---------------------------------------------------------------------------
# _resolve_empirica_version
# ---------------------------------------------------------------------------


def test_resolve_empirica_version_returns_a_version():
    """Smoke: the version resolver returns something non-empty."""
    v = _resolve_empirica_version()
    assert v
    assert v != "unknown"  # We're running inside an installed package


def test_resolve_empirica_version_matches_dunder_version():
    """The resolver should agree with empirica.__version__ when available."""
    from empirica import __version__
    assert _resolve_empirica_version() == __version__


def test_resolve_empirica_version_callable_documented_unknown_fallback():
    """The resolver documents an 'unknown' fallback when both empirica.__version__
    and importlib.metadata.version fail. We don't simulate that here (mocking
    in-process __version__ breaks subsequent imports), but we lock in that the
    function is callable and returns a non-empty string. The fallback path is
    inspected in code review."""
    from empirica.cli.command_handlers import setup_claude_code
    assert callable(setup_claude_code._resolve_empirica_version)
    result = setup_claude_code._resolve_empirica_version()
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# _render_versioned_template
# ---------------------------------------------------------------------------


def test_render_substitutes_empirica_version_placeholder(tmp_path):
    src = tmp_path / "src.md"
    src.write_text(
        "# Header v{{ empirica_version }}\n\n"
        "Syncs with: Empirica v{{ empirica_version }}\n",
        encoding="utf-8",
    )
    dst = tmp_path / "dst.md"

    with patch(
        "empirica.cli.command_handlers.setup_claude_code._resolve_empirica_version",
        return_value="9.9.9",
    ):
        _render_versioned_template(src, dst)

    out = dst.read_text(encoding="utf-8")
    assert "{{ empirica_version }}" not in out
    assert "v9.9.9" in out
    assert out.count("v9.9.9") == 2  # both placeholders replaced


def test_render_substitutes_generated_date_placeholder(tmp_path):
    src = tmp_path / "src.md"
    src.write_text("Generated: {{ generated_date }}\n", encoding="utf-8")
    dst = tmp_path / "dst.md"

    _render_versioned_template(src, dst)
    out = dst.read_text(encoding="utf-8")
    assert "{{ generated_date }}" not in out
    # Date format YYYY-MM-DD
    assert re.search(r"Generated: \d{4}-\d{2}-\d{2}", out)


def test_render_passes_through_text_with_no_placeholders(tmp_path):
    src = tmp_path / "src.md"
    src.write_text("Plain content with no placeholders.\n", encoding="utf-8")
    dst = tmp_path / "dst.md"

    _render_versioned_template(src, dst)
    assert dst.read_text(encoding="utf-8") == "Plain content with no placeholders.\n"


def test_render_idempotent_when_run_twice(tmp_path):
    src = tmp_path / "src.md"
    src.write_text("v{{ empirica_version }}\n", encoding="utf-8")
    dst = tmp_path / "dst.md"

    with patch(
        "empirica.cli.command_handlers.setup_claude_code._resolve_empirica_version",
        return_value="2.0.0",
    ):
        _render_versioned_template(src, dst)
        first = dst.read_text(encoding="utf-8")
        _render_versioned_template(src, dst)
        second = dst.read_text(encoding="utf-8")

    assert first == second
    assert first == "v2.0.0\n"


def test_render_does_not_substitute_in_dst_only(tmp_path):
    """Re-rendering should never re-process already-rendered content (defensive
    test — confirms src is read fresh each time, not the dst)."""
    src = tmp_path / "src.md"
    src.write_text("v{{ empirica_version }}\n", encoding="utf-8")
    dst = tmp_path / "dst.md"

    with patch(
        "empirica.cli.command_handlers.setup_claude_code._resolve_empirica_version",
        return_value="1.0.0",
    ):
        _render_versioned_template(src, dst)
    # src is unchanged; dst has the rendered version
    assert "{{ empirica_version }}" in src.read_text()
    assert "v1.0.0" in dst.read_text()


# ---------------------------------------------------------------------------
# Real templates: confirm placeholders match what setup-claude-code expects
# ---------------------------------------------------------------------------


def _project_template_path(name: str) -> Path:
    return (
        Path(__file__).parent.parent
        / "empirica" / "plugins" / "claude-code-integration"
        / "templates" / name
    )


def test_lean_template_uses_version_placeholder():
    """Sanity: the actual lean template has the placeholder, not a hardcoded
    version. If anyone reverts to a hardcoded version, this test fails."""
    template = _project_template_path("empirica-system-prompt-lean.md")
    text = template.read_text(encoding="utf-8")
    assert "{{ empirica_version }}" in text
    # No old hardcoded v1.x.x patterns in the first 5 lines (header)
    header = "\n".join(text.splitlines()[:5])
    assert not re.search(r"v1\.[0-9]\.[0-9]", header), (
        "Header contains hardcoded version — should use {{ empirica_version }} placeholder"
    )


def test_no_monolithic_claude_md_template():
    """The monolithic full-prompt template was removed (1.12.x): the lean
    template is the only system-prompt source, @included from CLAUDE.md. A
    self-contained prompt for a non-Claude harness is out of scope (community
    territory). This guards against the duplicate reappearing."""
    template = _project_template_path("CLAUDE.md")
    assert not template.exists(), (
        "templates/CLAUDE.md (monolithic full-prompt) should not exist — "
        "the lean template is the single source, @included from the user's CLAUDE.md"
    )


def test_real_lean_template_renders_without_placeholders_remaining(tmp_path):
    """End-to-end on the real template file: after rendering, no placeholders
    survive."""
    template = _project_template_path("empirica-system-prompt-lean.md")
    dst = tmp_path / "rendered.md"
    _render_versioned_template(template, dst)
    text = dst.read_text(encoding="utf-8")
    assert "{{ empirica_version }}" not in text
    assert "{{ generated_date }}" not in text
    # And the version actually appears in the header
    from empirica import __version__
    assert __version__ in text


# ---------------------------------------------------------------------------
# Conditional cortex guidance (goal 4eeb394e — 2026-06-03)
# ---------------------------------------------------------------------------


def test_cortex_off_strips_conditional_blocks(tmp_path):
    """With cortex disabled, `{% if cortex %}…{% endif %}` blocks are
    removed from the rendered output, tags-and-all."""
    src = tmp_path / "src.md"
    src.write_text(
        "before\n"
        "{% if cortex %}cortex-only guidance{% endif %}\n"
        "after\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.md"
    _render_versioned_template(src, dst, cortex_enabled=False)
    text = dst.read_text(encoding="utf-8")
    assert "cortex-only guidance" not in text
    assert "{% if cortex %}" not in text
    assert "{% endif %}" not in text
    assert "before" in text
    assert "after" in text


def test_cortex_on_keeps_block_content_strips_tags(tmp_path):
    """With cortex enabled, the block content is kept but the tags are
    stripped (so the rendered prompt reads cleanly)."""
    src = tmp_path / "src.md"
    src.write_text(
        "before\n"
        "{% if cortex %}cortex-only guidance{% endif %}\n"
        "after\n",
        encoding="utf-8",
    )
    dst = tmp_path / "out.md"
    _render_versioned_template(src, dst, cortex_enabled=True)
    text = dst.read_text(encoding="utf-8")
    assert "cortex-only guidance" in text
    assert "{% if cortex %}" not in text
    assert "{% endif %}" not in text


def test_real_lean_template_strips_mesh_precondition_when_cortex_off(tmp_path):
    """End-to-end: the real lean template's Mesh-active precondition + the
    cortex-mailbox skill rows disappear when cortex is off."""
    template = _project_template_path("empirica-system-prompt-lean.md")
    dst = tmp_path / "rendered.md"
    _render_versioned_template(template, dst, cortex_enabled=False)
    text = dst.read_text(encoding="utf-8")
    assert "Mesh-active precondition" not in text
    assert "/cortex-mailbox-poll" not in text
    assert "/cortex-mailbox-send" not in text
    # Practice-model + epistemic substrate stays
    assert "epistemic" in text.lower()
    assert "PREFLIGHT" in text


def test_real_lean_template_includes_mesh_precondition_when_cortex_on(tmp_path):
    """Inverse: with cortex on, the mesh-active precondition + the mailbox
    skill loading rows appear."""
    template = _project_template_path("empirica-system-prompt-lean.md")
    dst = tmp_path / "rendered.md"
    _render_versioned_template(template, dst, cortex_enabled=True)
    text = dst.read_text(encoding="utf-8")
    assert "Mesh-active precondition" in text
    assert "/cortex-mailbox-poll" in text
    assert "/cortex-mailbox-send" in text

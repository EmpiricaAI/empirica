"""Tests for the body_skill merge in loop install-requests.

When the install-request handler is given a body_skill (or auto-resolves
one from the canonical catalog), the resulting prompt_template should be
the skill's actual `## Cron Prompt Template` section — not the generic
template with a `[... your actual work here ...]` placeholder.

This closes the gap David surfaced: 'how does the AI know to load the
skill when the loop fires?' Answer: it doesn't — the skill body gets
baked into the install-request at install time, then travels with the
/loop --prompt to CronCreate. The body IS the prompt.
"""

from __future__ import annotations

from empirica.core.cockpit.loop_install_request import (
    _extract_skill_prompt_template,
    render_loop_cron_prompt,
)


def test_extract_known_canonical_skill_returns_cortex_body():
    """The canonical cortex-mailbox-poll skill exists in the repo and
    has a `## Cron Prompt Template` section with a code block. The
    extractor should find it and return the code-block contents."""
    body = _extract_skill_prompt_template('cortex-mailbox-poll')
    assert body is not None, (
        "expected to find cortex-mailbox-poll skill — "
        "did the canonical SKILL.md move?"
    )
    # The skill body invokes the MCP polling tools by name
    assert 'cortex_inbox_poll' in body
    assert 'cortex_outbox_poll' in body
    # And the self-throttle check
    assert 'transaction' in body.lower()


def test_extract_unknown_skill_returns_none():
    """Skill that doesn't exist on disk → None (graceful fall-through)."""
    body = _extract_skill_prompt_template('this-skill-does-not-exist-zzz9')
    assert body is None


def test_render_loop_cron_prompt_with_known_skill_uses_skill_body():
    """When body_skill is given AND the skill exists, the rendered
    prompt should be the skill body verbatim — NOT the generic template
    with a `[... your actual work ...]` placeholder."""
    rendered = render_loop_cron_prompt(
        name='cortex-mailbox-poll',
        interval='30s',
        body_skill='cortex-mailbox-poll',
    )
    # Skill body present
    assert 'cortex_inbox_poll' in rendered
    # Generic placeholder is GONE
    assert 'your actual work here' not in rendered


def test_render_loop_cron_prompt_with_unknown_skill_falls_back():
    """When body_skill is given BUT the skill doesn't exist, fall back
    to the generic template (don't crash)."""
    rendered = render_loop_cron_prompt(
        name='some-loop',
        interval='15m',
        body_skill='this-skill-does-not-exist-zzz9',
    )
    # Generic placeholder IS there
    assert 'your actual work here' in rendered
    # And the loop name was substituted into register call
    assert 'some-loop' in rendered


def test_render_loop_cron_prompt_without_body_skill_uses_generic():
    """Default behavior (no body_skill arg) renders the generic template
    so CLI users without a paired skill still get a useful starting
    point."""
    rendered = render_loop_cron_prompt(
        name='custom-loop',
        interval='15m',
    )
    assert 'your actual work here' in rendered
    assert 'custom-loop' in rendered


def test_handler_auto_resolves_body_skill_from_canonical_catalog(tmp_path, monkeypatch):
    """The CLI handler should auto-look-up body_skill from
    canonical_loops.CANONICAL_LOOPS when not explicitly given —
    so CLI users running `empirica loop install-request --name
    cortex-mailbox-poll --interval 30s` get the skill body baked in
    without needing to pass --body-skill."""
    from types import SimpleNamespace

    from empirica.cli.command_handlers.cockpit_commands import (
        handle_loop_install_request_command,
    )

    # Redirect the per-instance state files to a tmpdir
    monkeypatch.setattr(
        'empirica.core.cockpit.loop_install_request.EMPIRICA_DIR',
        tmp_path,
    )
    monkeypatch.setattr(
        'empirica.core.cockpit.loop_registry.EMPIRICA_DIR',
        tmp_path,
    )

    args = SimpleNamespace(
        instance='tmux_test',
        name='cortex-mailbox-poll',
        interval='30s',
        description='',
        base_interval=None,
        max_interval=None,
        # body_skill NOT provided — should auto-resolve from canonical catalog
        output='json',
        verbose=False,
    )
    rc = handle_loop_install_request_command(args)
    assert rc == 0 or rc is None  # success

    # The pending file should now exist with the cortex body
    pending = tmp_path / 'loop_install_pending_tmux_test_cortex-mailbox-poll.json'
    assert pending.exists()

    import json
    data = json.loads(pending.read_text())
    template = data.get('prompt_template', '')
    assert 'cortex_inbox_poll' in template, (
        "canonical body_skill should have been auto-resolved + baked in"
    )
    assert 'your actual work here' not in template

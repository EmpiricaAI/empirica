"""Tests for `empirica mailbox reply` (prop_rau4ymp62fhenavyolejadahtq).

Verifies the atomic propose+complete shape, smart defaults from parent,
--no-close opt-out, and partial-failure surfacing.
"""

from __future__ import annotations

import types

from empirica.cli.command_handlers.mailbox_commands import (
    handle_mailbox_reply_command,
)

# ─── Test infrastructure ──────────────────────────────────────────────


def _make_args(**overrides):
    """Build a Namespace with all fields the handler reads."""
    defaults = {
        "parent_id": "prop_parent",
        "summary": "test summary",
        "title": None,
        "type": "collab_brief",
        "target_claudes": None,
        "source_claude": None,
        "payload": None,
        "result": "shipped",
        "commit_sha": None,
        "no_close": False,
        "no_archive": False,
        "output": "json",
    }
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def _record_post():
    """Returns (calls, fn) — fn records every POST call."""
    calls = []

    def fn(url, body, api_key, timeout):
        calls.append({
            "url": url, "body": body, "api_key": api_key, "timeout": timeout,
        })
        if "/propose" in url:
            return 200, {"ok": True, "proposal_id": "prop_new_xyz",
                         "status": "accepted", "ntfy_emitted": True}
        if "/complete" in url:
            return 200, {"ok": True, "proposal_id": "prop_parent",
                         "status": "completed"}
        if "/archive" in url:
            return 200, {"ok": True, "proposal_id": "prop_parent",
                         "status": "archived"}
        return 404, {"ok": False, "error": "unknown"}
    return calls, fn


def _creds(url="https://cortex.example.com", key="ctx_test"):
    return lambda: (url, key)


def _ai_id(value="empirica"):
    return lambda: value


def _fetch_parent(source_claude="extension", title="Parent Title"):
    return lambda url, key, pid, timeout=5.0: {
        "id": pid, "source_claude": source_claude, "title": title,
    }


# ─── Happy path ───────────────────────────────────────────────────────


def test_reply_atomic_propose_and_complete(capsys):
    calls, post = _record_post()
    rc = handle_mailbox_reply_command(
        _make_args(),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    assert rc == 0
    # Both endpoints hit
    urls = [c["url"] for c in calls]
    assert any("/v1/orchestration/propose" in u for u in urls)
    assert any("/v1/orchestration/prop_parent/complete" in u for u in urls)
    # Output contains expected ids
    out = capsys.readouterr().out
    assert "prop_new_xyz" in out
    assert '"parent_closed": true' in out


def test_reply_smart_default_title(capsys):
    calls, post = _record_post()
    handle_mailbox_reply_command(
        _make_args(title=None),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(title="Original Question"),
    )
    propose = next(c for c in calls if "/propose" in c["url"])
    assert propose["body"]["title"] == "Re: Original Question"


def test_reply_smart_default_target_claudes(capsys):
    calls, post = _record_post()
    handle_mailbox_reply_command(
        _make_args(target_claudes=None),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(source_claude="cortex"),
    )
    propose = next(c for c in calls if "/propose" in c["url"])
    assert propose["body"]["target_claudes"] == ["cortex"]


def test_reply_source_claude_from_project_yaml(capsys):
    calls, post = _record_post()
    handle_mailbox_reply_command(
        _make_args(),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id("my-ai"),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    propose = next(c for c in calls if "/propose" in c["url"])
    assert propose["body"]["source_claude"] == "my-ai"


def test_reply_passes_parent_id_in_propose(capsys):
    calls, post = _record_post()
    handle_mailbox_reply_command(
        _make_args(parent_id="prop_xyz"),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    propose = next(c for c in calls if "/propose" in c["url"])
    assert propose["body"]["parent_id"] == "prop_xyz"


def test_reply_explicit_overrides(capsys):
    calls, post = _record_post()
    handle_mailbox_reply_command(
        _make_args(
            title="Custom title",
            target_claudes="extension,cortex",
            source_claude="explicit-ai",
            type="code_change_request",
        ),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id("default-ai"),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    propose = next(c for c in calls if "/propose" in c["url"])
    assert propose["body"]["title"] == "Custom title"
    assert propose["body"]["target_claudes"] == ["extension", "cortex"]
    assert propose["body"]["source_claude"] == "explicit-ai"
    assert propose["body"]["type"] == "code_change_request"


def test_reply_title_truncated_at_200_chars(capsys):
    calls, post = _record_post()
    long_title = "Re: " + ("x" * 300)
    handle_mailbox_reply_command(
        _make_args(title=long_title),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    propose = next(c for c in calls if "/propose" in c["url"])
    assert len(propose["body"]["title"]) == 200
    assert propose["body"]["title"].endswith("...")


# ─── --no-close opt-out ───────────────────────────────────────────────


def test_no_close_skips_complete_call(capsys):
    calls, post = _record_post()
    rc = handle_mailbox_reply_command(
        _make_args(no_close=True),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    assert rc == 0
    urls = [c["url"] for c in calls]
    assert any("/propose" in u for u in urls)
    assert not any("/complete" in u for u in urls)
    assert not any("/archive" in u for u in urls)  # no close → no archive
    out = capsys.readouterr().out
    assert '"parent_closed": false' in out
    assert '"parent_archived": false' in out
    assert '"result": null' in out


# ─── Auto-archive on completion (default) ─────────────────────────────


def test_default_auto_archives_after_complete(capsys):
    """Default behaviour: after parent close, also archive it."""
    calls, post = _record_post()
    rc = handle_mailbox_reply_command(
        _make_args(),  # no_archive defaults to False
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    assert rc == 0
    urls = [c["url"] for c in calls]
    assert any("/v1/orchestration/prop_parent/archive" in u for u in urls)
    out = capsys.readouterr().out
    assert '"parent_closed": true' in out
    assert '"parent_archived": true' in out


def test_no_archive_keeps_parent_visible(capsys):
    """--no-archive: close the parent but skip the archive step."""
    calls, post = _record_post()
    rc = handle_mailbox_reply_command(
        _make_args(no_archive=True),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    assert rc == 0
    urls = [c["url"] for c in calls]
    assert any("/complete" in u for u in urls)
    assert not any("/archive" in u for u in urls)
    out = capsys.readouterr().out
    assert '"parent_closed": true' in out
    assert '"parent_archived": false' in out


def test_archive_failure_does_not_block_reply(capsys):
    """If archive POST fails, propose+complete results still surface ok."""
    calls = []

    def post(url, body, api_key, timeout):
        calls.append({"url": url})
        if "/propose" in url:
            return 200, {"ok": True, "proposal_id": "prop_new_xyz"}
        if "/complete" in url:
            return 200, {"ok": True}
        if "/archive" in url:
            return 500, {"ok": False, "error": "internal"}
        return 404, {"ok": False}

    rc = handle_mailbox_reply_command(
        _make_args(),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    assert rc == 0  # reply still succeeds
    out = capsys.readouterr().out
    assert '"parent_closed": true' in out
    assert '"parent_archived": false' in out


# ─── Failure modes ────────────────────────────────────────────────────


def test_missing_parent_id_errors():
    rc = handle_mailbox_reply_command(
        _make_args(parent_id=None),
        _resolve_cortex_creds=_creds(),
    )
    assert rc == 1


def test_missing_summary_errors():
    rc = handle_mailbox_reply_command(
        _make_args(summary=None),
        _resolve_cortex_creds=_creds(),
    )
    assert rc == 1


def test_missing_cortex_creds_errors(capsys):
    rc = handle_mailbox_reply_command(
        _make_args(),
        _resolve_cortex_creds=lambda: (None, None),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "Cortex creds missing" in err


def test_missing_source_claude_errors(capsys):
    rc = handle_mailbox_reply_command(
        _make_args(source_claude=None),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=lambda: None,
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "source_claude unresolved" in err


def test_parent_not_found_errors(capsys):
    rc = handle_mailbox_reply_command(
        _make_args(),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _fetch_parent=lambda url, key, pid, timeout=5.0: None,
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_parent_without_source_claude_and_no_override_errors(capsys):
    rc = handle_mailbox_reply_command(
        _make_args(target_claudes=None),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _fetch_parent=lambda url, key, pid, timeout=5.0: {
            "id": pid, "source_claude": None, "title": "Untitled",
        },
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "target_claudes empty" in err


def test_invalid_payload_json_errors(capsys):
    rc = handle_mailbox_reply_command(
        _make_args(payload="not-json"),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _fetch_parent=_fetch_parent(),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "not valid JSON" in err


def test_propose_failure_returns_1(capsys):
    def failing_post(url, body, api_key, timeout):
        return 500, {"ok": False, "error": "server explosion"}
    rc = handle_mailbox_reply_command(
        _make_args(),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=failing_post,
        _fetch_parent=_fetch_parent(),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "cortex_propose failed" in err


def test_complete_failure_returns_0_with_warning(capsys):
    """Partial success — propose worked, complete failed. Don't fail hard."""
    def split_post(url, body, api_key, timeout):
        if "/propose" in url:
            return 200, {"ok": True, "proposal_id": "prop_new_xyz",
                         "status": "accepted"}
        # complete fails
        return 502, {"ok": False, "error": "cortex unreachable"}
    rc = handle_mailbox_reply_command(
        _make_args(),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=split_post,
        _fetch_parent=_fetch_parent(),
    )
    assert rc == 0  # propose did succeed
    captured = capsys.readouterr()
    assert "but parent close FAILED" in captured.err
    assert '"parent_closed": false' in captured.out


def test_human_output_format(capsys):
    _, post = _record_post()
    handle_mailbox_reply_command(
        _make_args(output="human"),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    out = capsys.readouterr().out
    assert "reply" in out
    assert "prop_new_xyz" in out
    assert "closed" in out
    assert "prop_parent" in out


def test_commit_sha_attached_to_completion(capsys):
    calls, post = _record_post()
    handle_mailbox_reply_command(
        _make_args(commit_sha="abc123def"),
        _resolve_cortex_creds=_creds(),
        _resolve_ai_id=_ai_id(),
        _http_post=post,
        _fetch_parent=_fetch_parent(),
    )
    complete = next(c for c in calls if "/complete" in c["url"])
    assert complete["body"]["commit_sha"] == "abc123def"

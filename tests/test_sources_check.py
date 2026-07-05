"""Tests for `empirica sources-check` — source link-rot detection (hygiene WS1).

Dependency-injected (`_list_sources` / `_probe`) — no network. Verifies the
surface-only receipt, the dead→exit-1 gate, and that gated/errored/non-URL
sources don't fail the run.
"""

from __future__ import annotations

import json
import types

from empirica.cli.command_handlers.sources_check_commands import (
    _classify_status,
    _is_probeable,
    handle_sources_check_command,
)


def _args(**overrides):
    defaults = {"project_id": "proj-1", "timeout": 6.0, "output": "json"}
    defaults.update(overrides)
    return types.SimpleNamespace(**defaults)


def _sources(*rows):
    return list(rows)


def _src(sid, url, title="t"):
    return {"id": sid, "url": url, "title": title}


# ── classification ──────────────────────────────────────────────────────


def test_classify_status():
    assert _classify_status(200)[0] == "live"
    assert _classify_status(301)[0] == "live"  # redirect resolves
    assert _classify_status(403)[0] == "gated"
    assert _classify_status(401)[0] == "gated"
    assert _classify_status(404)[0] == "dead"
    assert _classify_status(410)[0] == "dead"
    assert _classify_status(500)[0] == "error"


def test_is_probeable():
    assert _is_probeable("https://x.com") is True
    assert _is_probeable("http://x.com") is True
    assert _is_probeable("/local/path.md") is False
    assert _is_probeable("mailto:a@b.com") is False
    assert _is_probeable(None) is False


# ── handler ─────────────────────────────────────────────────────────────


def test_all_live_exits_zero(capsys):
    srcs = _sources(_src("s1", "https://a.com"), _src("s2", "https://b.com"))
    rc = handle_sources_check_command(
        _args(),
        _list_sources=lambda pid: srcs,
        _probe=lambda url, t: ("live", "200"),
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checked"] == 2
    assert out["live"] == 2
    assert out["dead"] == []


def test_dead_link_exits_one(capsys):
    srcs = _sources(_src("s1", "https://ok.com"), _src("s2", "https://gone.com", "Gone"))

    def probe(url, t):
        return ("dead", "404") if "gone" in url else ("live", "200")

    rc = handle_sources_check_command(_args(), _list_sources=lambda pid: srcs, _probe=probe)
    assert rc == 1  # dead → gate fails
    out = json.loads(capsys.readouterr().out)
    assert out["live"] == 1
    assert len(out["dead"]) == 1
    assert out["dead"][0]["id"] == "s2"
    assert out["dead"][0]["url"] == "https://gone.com"


def test_gated_and_errored_do_not_fail(capsys):
    srcs = _sources(_src("s1", "https://paywall.com"), _src("s2", "https://flaky.com"))

    def probe(url, t):
        return ("gated", "403") if "paywall" in url else ("error", "500")

    rc = handle_sources_check_command(_args(), _list_sources=lambda pid: srcs, _probe=probe)
    assert rc == 0  # gated/errored surfaced but not a failure
    out = json.loads(capsys.readouterr().out)
    assert len(out["gated"]) == 1
    assert len(out["errored"]) == 1
    assert out["dead"] == []


def test_non_url_sources_skipped(capsys):
    srcs = _sources(
        _src("s1", "https://a.com"),
        _src("s2", "/local/doc.md"),  # not probeable
        _src("s3", "mailto:x@y.com"),  # not probeable
        {"id": "s4", "url": None, "title": "no url"},
    )
    rc = handle_sources_check_command(_args(), _list_sources=lambda pid: srcs, _probe=lambda url, t: ("live", "200"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["checked"] == 1  # only s1
    assert out["skipped_no_url"] == 3


def test_missing_project_id_exits_one(capsys, monkeypatch):
    # Force the session fallback to yield nothing (running from a real project
    # dir would otherwise resolve the active project_id).
    monkeypatch.setattr(
        "empirica.cli.command_handlers.sources_check_commands._resolve_project_id",
        lambda args: None,
    )
    rc = handle_sources_check_command(_args(project_id=None), _list_sources=lambda pid: [])
    assert rc == 1
    assert "project_id" in capsys.readouterr().err


def test_list_failure_surfaces(capsys):
    def boom(pid):
        raise RuntimeError("db locked")

    rc = handle_sources_check_command(_args(), _list_sources=boom)
    assert rc == 1
    assert "failed to list sources" in capsys.readouterr().err


def test_human_output(capsys):
    srcs = _sources(_src("s1", "https://gone.com", "Gone Doc"))
    rc = handle_sources_check_command(
        _args(output="human"),
        _list_sources=lambda pid: srcs,
        _probe=lambda url, t: ("dead", "404"),
    )
    assert rc == 1
    out = capsys.readouterr().out
    assert "sources-check" in out
    assert "DEAD" in out and "Gone Doc" in out

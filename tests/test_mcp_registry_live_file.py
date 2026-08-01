"""The MCP reader must read the file Claude Code actually maintains.

`doctor` and the scanner read `~/.claude/mcp.json` and `~/.claude/settings.json`.
Claude Code maintains `~/.claude.json`. On the box where this was found the former
was 822 bytes last written 2026-07-28 while the latter was 100KB rewritten the same
day — so doctor reported PASS about a file nobody reads.

That is the most expensive form of the wrong-authority bug: doctor is the instrument
people reach for to learn whether things are fine, so a verdict true of the wrong
subject suppresses the investigation that would have found the real state.

Two further properties fell out of fixing it, both measured rather than assumed:
registrations live in TWO scopes in that file (a global `mcpServers` block and
per-project blocks under `projects[<path>].mcpServers`), and reading the live file
alongside the legacy ones double-counted 3 of 10 rows.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from empirica.core.scanner import manifests


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """Point the reader at throwaway files, live-file-first like the real order."""

    def _install(live: dict | None = None, legacy: dict | None = None):
        paths = []
        if live is not None:
            p = tmp_path / "claude.json"
            p.write_text(json.dumps(live), encoding="utf-8")
            paths.append(p)
        if legacy is not None:
            q = tmp_path / "mcp.json"
            q.write_text(json.dumps(legacy), encoding="utf-8")
            paths.append(q)
        monkeypatch.setattr(manifests, "_MCP_REGISTRY_CANDIDATES", tuple(paths))
        return manifests._read_mcp_servers()

    return _install


def test_the_live_file_is_read_at_all():
    """POSITIVE CONTROL on the original bug: ~/.claude.json must be a candidate."""
    names = [p.name for p in manifests._MCP_REGISTRY_CANDIDATES]

    assert ".claude.json" in names, "the file Claude Code maintains is not read"
    assert names[0] == ".claude.json", "the live file must be preferred over the legacy ones"


def test_project_scoped_servers_are_not_missed(registry):
    """Registrations live in two scopes. Reading only the global block undercounts
    every project-scoped server — on the box where this was found, most of them."""
    rows = registry(
        live={
            "mcpServers": {"global-one": {"command": "x"}},
            "projects": {"/some/proj": {"mcpServers": {"proj-one": {"command": "y"}}}},
        }
    )

    by_name = {r["name"]: r for r in rows}
    assert set(by_name) == {"global-one", "proj-one"}
    assert by_name["global-one"]["scope"] == "global"
    assert by_name["proj-one"]["scope"] == "/some/proj"


def test_a_server_in_both_files_is_counted_once(registry):
    """NEGATIVE CONTROL on the fix's own side effect. Adding the live file without
    dedup double-counted 3 of 10 real rows, which would have made a composition read
    report servers a practice does not separately have."""
    rows = registry(
        live={"mcpServers": {"empirica": {"command": "live"}}},
        legacy={"mcpServers": {"empirica": {"command": "stale"}}},
    )

    assert len(rows) == 1
    assert rows[0]["command"] == "live", "the legacy file won — candidate order is wrong"


def test_legacy_only_servers_still_surface(registry):
    """The fallbacks are not decoration: an install that still uses mcp.json must keep
    working. Without this the fix could pass by ignoring the legacy files entirely."""
    rows = registry(live={"mcpServers": {}}, legacy={"mcpServers": {"old": {"command": "z"}}})

    assert [r["name"] for r in rows] == ["old"]


def test_every_row_carries_its_provenance(registry):
    """`source`, `scope` and `observed_at` make a row self-describing. Without them a
    consumer cannot tell a stale file's answer from a fresh one — which is the whole
    failure being fixed."""
    rows = registry(live={"mcpServers": {"a": {"command": "x"}}})

    assert rows[0]["source"].endswith("claude.json")
    assert rows[0]["scope"] == "global"
    assert rows[0]["observed_at"], "no observed_at — staleness stays invisible"


def test_a_malformed_file_is_skipped_not_fatal(registry, tmp_path, monkeypatch):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    good = tmp_path / "claude.json"
    good.write_text(json.dumps({"mcpServers": {"a": {"command": "x"}}}), encoding="utf-8")
    monkeypatch.setattr(manifests, "_MCP_REGISTRY_CANDIDATES", (bad, good))

    assert [r["name"] for r in manifests._read_mcp_servers()] == ["a"]


def test_the_real_candidate_paths_expand_to_absolute_home_paths():
    for p in manifests._MCP_REGISTRY_CANDIDATES:
        assert str(p).startswith(os.path.expanduser("~")), f"{p} is not under home"
        assert Path(p).is_absolute()

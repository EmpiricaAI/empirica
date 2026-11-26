"""Findings from the 1.17.19 pre-publish sweep (goal 738f1ee3)."""

from __future__ import annotations

import io
import json
import os

import pytest


def _tracker():
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/context-shift-tracker.py"
    )
    spec = importlib.util.spec_from_file_location("cst", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "prompt",
    ["Design doc review: is it accurate?", "design is fine", "Specify the flag name in the README", "Plan approved.",
     "plan mode", "then plan mode was on", "Plan B is fine", "now implement nothing"],
)  # fmt: skip
def test_mentions_do_not_read_as_plan_requests(prompt):
    assert not _tracker()._PLAN_REQUEST.search(prompt)


@pytest.mark.parametrize(
    "prompt",
    ["ok, implement it", "yes implement it", "let us plan the release", "help me plan the release",
     "we need to plan the migration", "I want you to implement the listener", "Plan: migrate db",
     "write a detailed plan", "draft an implementation plan", "break down the work", "Context:\nimplement X",
     "multi-step refactor", "Plan the next steps.", "implement X and Y"],
)  # fmt: skip
def test_approvals_and_requests_still_read_as_plan_requests(prompt):
    assert _tracker()._PLAN_REQUEST.search(prompt)


def test_the_group_check_compares_with_the_groups_this_process_holds(tmp_path, monkeypatch):
    """The login's database groups (getgrouplist) include grants not yet in any running process; an inline server would
    not hold them either, so only groups this process holds and the manager lacks count."""
    from empirica.core.cockpit.launcher import tmux

    d = tmp_path / "77"
    d.mkdir()
    (d / "status").write_text("Groups:\t100 200 \n")
    run = lambda argv, **kw: __import__("subprocess").CompletedProcess(argv, 0, "77\n", "")  # noqa: E731
    monkeypatch.setattr(os, "getgroups", lambda: [100, 200])
    monkeypatch.setattr(os, "getgrouplist", lambda *a: [100, 200, 978], raising=False)
    assert tmux.manager_missing_groups(run=run, proc_root=tmp_path) == set()


def test_an_unsynced_own_row_does_not_fall_through_to_another_practice(monkeypatch):
    from empirica.core.loop_scheduler import content_poll as cp

    roster = {"self": {"tenant_slug": "francisco"}, "org": {"tenants": [{"tenant_slug": "francisco", "projects": [
        {"ai_id_short": "empirica", "ai_id_mesh": None}]}]}}  # fmt: skip
    projects = {"projects": [{"slug": "empirica", "ai_id_canonical": "empirica.david.empirica"}]}

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        cp,
        "open_url",
        lambda req, timeout=None: R(json.dumps(roster if req.full_url.endswith("roster") else projects).encode()),
    )
    cp._CANONICAL_AI_ID_CACHE.clear()
    cp._CANONICAL_FAILED_AT.clear()
    assert cp._resolve_canonical_ai_id("https://c", "k", "empirica") == "empirica"


def test_an_ambiguous_slug_among_joined_practices_resolves_nothing(monkeypatch):
    from empirica.core.loop_scheduler import content_poll as cp

    projects = {
        "projects": [
            {"slug": "consult", "ai_id_canonical": "o.a.consult"},
            {"slug": "consult", "ai_id_canonical": "o.b.consult"},
        ]
    }

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(cp, "open_url", lambda req, timeout=None: R(json.dumps(projects).encode()))
    assert cp.joined_practice_canonical("https://c", "k", "consult") is None


def test_a_cross_org_peer_with_the_same_tenant_slug_is_not_own():
    from empirica.cli.command_handlers.practice_context_commands import _filter_rows_by_ai_id

    rows = [
        {"ai_id": "research", "tenant": "david", "org": "empirica", "ai_id_mesh": "empirica.david.research"},
        {"ai_id": "research", "tenant": "david", "org": "nle", "ai_id_mesh": "nle.david.research", "role": "cross_org"},
    ]
    roster = {"self": {"tenant_slug": "david"}, "org": {"slug": "empirica"}}
    assert [r["ai_id_mesh"] for r in _filter_rows_by_ai_id(rows, "research", roster)] == ["empirica.david.research"]


def test_a_seat_agreement_naming_another_tenants_practice_does_not_count():
    from empirica.cli.command_handlers.practice_context_commands import _cross_org_rows

    roster = {"self": {"tenant_slug": "david"}, "org": {"slug": "empirica"}, "cross_org_visible": [
        {"scope": "seat_seat", "state": "active", "other_party": {"org_slug": "s", "reachable_from": "acme.bob.research"},
         "practices": [{"canonical_ai_id_mesh": "s.t.x", "slug": "x"}]}]}  # fmt: skip
    assert _cross_org_rows(roster, "research", set()) == []


def test_a_url_remote_is_checked_for_a_public_host(monkeypatch):
    from empirica.cli.command_handlers import sync_commands as sc

    monkeypatch.setattr(sc, "_get_remote_url", lambda r: None)
    monkeypatch.setattr(sc, "_load_sync_config", lambda: {})
    assert sc.public_notes_remote("https://github.com/me/notes.git")
    assert sc.public_notes_remote("git@gitlab.com:me/notes.git")


def test_a_refused_profile_sync_push_is_not_a_success(monkeypatch, capsys):
    from types import SimpleNamespace

    from empirica.cli.command_handlers import profile_commands as pc
    from empirica.cli.command_handlers import sync_commands as sc

    monkeypatch.setattr(pc, "_push_notes", lambda remote: {"ok": True})
    monkeypatch.setattr(pc, "_import_notes_to_sqlite", lambda: {"ok": True})
    monkeypatch.setattr(pc, "_fetch_notes", lambda remote: {"ok": True})
    monkeypatch.setattr(sc, "public_notes_remote", lambda remote: "github.com")
    rc = pc.handle_profile_sync_command(
        SimpleNamespace(push=True, qdrant=False, import_only=False, force=False, remote="origin", output="text")
    )
    out = capsys.readouterr().out
    assert rc == 1 and "Pushed to" not in out


def test_logout_drops_a_leftover_key(tmp_path, monkeypatch):
    import yaml

    from empirica.cli.command_handlers import auth_commands

    target = tmp_path / "credentials.yaml"
    target.write_text("cortex:\n  url: https://c\n  api_key: ctx_old\n  oauth: {access_token: a}\n")

    class L:
        def _find_config_file(self, *a):
            return target

        def _read_existing(self, t):
            return yaml.safe_load(t.read_text())

        def _write_credentials(self, t, data):
            t.write_text(yaml.safe_dump(data))
            return t

        def multi_team_allowed(self):
            return False

    auth_commands._drop_oauth_block(L(), None)
    assert "api_key" not in yaml.safe_load(target.read_text())["cortex"]

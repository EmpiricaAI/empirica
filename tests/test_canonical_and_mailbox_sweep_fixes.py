"""Phase 1 sweep fixes, canonical and mailbox (goal 93823a59): each defect reproduced at HEAD by an independent read-only run on 2026-10-25
(not just trusted from the 2026-10-05 report), then pinned here with a negative control. Everything is built under tmp_path with HOME pinned.
"""

from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import subprocess
import urllib.error
import urllib.request
from types import SimpleNamespace as NS

import pytest


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("HOME", str(h))
    return h


# ── C1 turn threading ────────────────────────────────────────────────────────


def _jsonl(tmp_path, records):
    p = tmp_path / "s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records))
    return str(p)


def _user(uid, content):
    return {"type": "user", "uuid": uid, "timestamp": f"t{uid}", "message": {"role": "user", "content": content}}


def _assistant(uid, blocks):
    return {
        "type": "assistant",
        "uuid": uid,
        "timestamp": f"t{uid}",
        "message": {"role": "assistant", "model": "m", "content": blocks},
    }


def _turns(path):
    from empirica.core.canonical.transcript_parser import TranscriptParser

    parser = TranscriptParser()
    return list(parser.iter_conversation_turns(parser.parse_session(path)))


def test_a_tool_result_does_not_start_a_new_turn_and_nothing_after_it_is_dropped(tmp_path):
    records = [
        _user("1", "do thing"),
        _assistant(
            "2",
            [
                {"type": "text", "text": "A1 before tool"},
                {"type": "tool_use", "id": "tu1", "name": "Bash", "input": {}},
            ],
        ),
        _user("3", [{"type": "tool_result", "tool_use_id": "tu1", "content": "out"}]),
        _assistant(
            "4",
            [
                {"type": "thinking", "thinking": "THINK2"},
                {"type": "text", "text": "A2 after tool"},
                {"type": "tool_use", "id": "tu2", "name": "Read", "input": {}},
            ],
        ),
        _user("5", [{"type": "tool_result", "tool_use_id": "tu2", "content": "out2"}]),
        _assistant("6", [{"type": "text", "text": "A3 final answer"}]),
    ]
    turns = _turns(_jsonl(tmp_path, records))
    assert len(turns) == 1, "one user prompt is one turn, however many tool calls follow"
    t = turns[0]
    assert t.user_message == "do thing"
    assert all(s in t.assistant_text for s in ("A1 before tool", "A2 after tool", "A3 final answer"))
    assert "THINK2" in t.thinking
    assert len(t.tool_chains) == 2


def test_two_real_user_prompts_are_still_two_turns(tmp_path):
    records = [
        _user("1", "first"),
        _assistant("2", [{"type": "text", "text": "answer one"}]),
        _user("3", "second"),
        _assistant("4", [{"type": "text", "text": "answer two"}]),
    ]
    turns = _turns(_jsonl(tmp_path, records))
    assert [t.user_message for t in turns] == ["first", "second"]
    assert [t.assistant_text for t in turns] == ["answer one", "answer two"]


# ── C2 resolution state survives a from-notes import ─────────────────────────


def _findings_db():
    from empirica.data.schema.projects_schema import SCHEMAS

    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys=OFF")
    for sql in SCHEMAS:
        if "CREATE TABLE" in sql and "project_findings" in sql.split("(")[0]:
            conn.execute(sql)
    return NS(conn=conn)


@pytest.fixture
def repo(tmp_path):
    w = tmp_path / "repo"
    w.mkdir()

    def git(*a):
        subprocess.run(["git", *a], cwd=w, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "a@b")
    git("config", "user.name", "x")
    git("commit", "--allow-empty", "-qm", "init")
    return str(w)


def _store(repo, fid):
    from empirica.core.canonical.empirica_git.finding_store import GitFindingStore

    s = GitFindingStore(workspace_root=repo)
    assert s.store_finding(
        finding_id=fid, project_id="p", session_id="s", ai_id="a", finding=f"finding {fid}", impact=0.5
    )
    return s


def _rows(db):
    return {
        r[0]: r[1:]
        for r in db.conn.execute(
            "SELECT id, is_resolved, resolution, resolution_kind, superseded_by FROM project_findings"
        )
    }


def test_a_resolved_finding_comes_back_resolved_from_notes_with_its_reason_kind_and_replacement(repo):
    from empirica.core.canonical.empirica_git.profile_import import ProfileImporter

    s = _store(repo, "f-resolved")
    assert s.resolve_finding(
        "f-resolved", "retracted: it was false", superseded_by="f-new", resolution_kind="retracted"
    )
    db = _findings_db()
    ProfileImporter(workspace_root=repo).import_findings(db)
    is_resolved, resolution, kind, superseded_by = _rows(db)["f-resolved"]
    assert is_resolved == 1
    assert resolution == "retracted: it was false"
    assert kind == "retracted"
    assert superseded_by == "f-new"


def test_an_open_finding_stays_open_through_the_same_import(repo):
    from empirica.core.canonical.empirica_git.profile_import import ProfileImporter

    _store(repo, "f-open")
    db = _findings_db()
    ProfileImporter(workspace_root=repo).import_findings(db)
    assert _rows(db)["f-open"] == (0, None, None, None)


def test_the_sqlite_resolve_path_writes_the_resolution_kind_into_the_note(repo, monkeypatch):
    """Without this the note never carried the kind, so even a fixed importer rebuilt `retracted` as NULL."""
    from empirica.core.canonical.empirica_git.finding_store import GitFindingStore

    s = _store(repo, "f-kind")
    s.resolve_finding("f-kind", "stale: aged", resolution_kind="stale")
    note = GitFindingStore(workspace_root=repo).load_finding("f-kind")
    assert note["is_resolved"] is True and note["resolution_kind"] == "stale"


# ── C4 the reason a credential is unusable reaches the user ──────────────────


def test_an_unusable_api_key_says_why_on_stderr_instead_of_telling_the_user_to_configure_it(monkeypatch):
    import empirica.cli.command_handlers.mailbox_commands as m
    import empirica.core.auth as auth

    reason = "api_key unusable (revoked 401): re-authenticate with `empirica auth login`"
    monkeypatch.setattr(
        auth,
        "cortex_bearer",
        lambda *a, **k: {"url": "https://cortex.example", "bearer": None, "source": "none", "reason": reason},
    )
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        url, bearer = m._default_resolve_cortex_creds()
    assert (url, bearer) == ("https://cortex.example", None), "the 2-tuple contract is unchanged"
    assert "revoked 401" in err.getvalue()


def test_a_usable_bearer_writes_nothing_to_stderr(monkeypatch):
    import empirica.cli.command_handlers.mailbox_commands as m
    import empirica.core.auth as auth

    monkeypatch.setattr(
        auth, "cortex_bearer", lambda *a, **k: {"url": "https://cortex.example", "bearer": "tok", "source": "oauth"}
    )
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert m._default_resolve_cortex_creds() == ("https://cortex.example", "tok")
    assert err.getvalue() == ""


# ── C5 the own-proposal guard survives a roster outage ───────────────────────

CANON = "empirica.david.empirica-cortex"
ROSTER_OK = {
    "self": {"tenant_slug": "david"},
    "org": {
        "tenants": [
            {
                "tenant_slug": "david",
                "projects": [{"slug": "empirica-cortex", "ai_id_short": "empirica-cortex", "ai_id_mesh": CANON}],
            }
        ]
    },
}


def _reply(roster, parent_source=CANON):
    import empirica.cli.command_handlers.mailbox_commands as m

    posts = []

    def post(url, body, key, t=10.0):
        posts.append((url.rsplit("/", 1)[-1], body.get("target_claudes"), body.get("source_claude")))
        return 200, {"ok": True}

    err = io.StringIO()
    args = NS(
        parent_id="prop_x", summary="s", source_claude=None, target_claudes=None, title=None, type=None, payload=None,
        no_close=False, result=None, output="json",
    )  # fmt: skip
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = m.handle_mailbox_reply_command(
            args,
            _resolve_cortex_creds=lambda: ("https://c", "k"),
            _resolve_ai_id=lambda: "empirica-cortex",
            _http_post=post,
            _http_get=lambda u, k, t: roster,
            _fetch_parent=lambda u, k, p: {
                "source_claude": parent_source,
                "title": "T",
                "target_claudes": ["empirica.david.empirica"],
            },
        )
    return rc, posts, err.getvalue()


@pytest.mark.parametrize("roster", [(503, {"error": "x"}), (-1, {"error": "timeout"})])
def test_when_the_roster_is_down_a_reply_to_your_own_proposal_is_refused_not_posted_to_yourself(roster):
    rc, posts, err = _reply(roster)
    assert rc == 1
    assert posts == [], "no propose may be sent while the guard cannot tell whose proposal this is"
    assert "roster" in err.lower() or "canonical" in err.lower()


def test_with_the_roster_up_the_guard_still_fires_and_a_peers_proposal_still_posts():
    rc, posts, _ = _reply((200, ROSTER_OK))
    assert rc == 1 and posts == []
    rc, posts, _ = _reply((200, ROSTER_OK), parent_source="empirica.david.empirica-extension")
    assert len(posts) == 1 and posts[0][1] == ["empirica.david.empirica-extension"]


# ── C6 an unresolved canonical id is not an empty inbox ──────────────────────


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def _poll(monkeypatch, roster_down: bool, roster=None):
    import empirica.cli.command_handlers.mailbox_commands as m
    from empirica.core.loop_scheduler import content_poll as cp

    seen = []

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        seen.append(url)
        if "roster" in url:
            if roster_down:
                raise urllib.error.URLError("roster down")
            return _Resp(roster or ROSTER_OK)
        return _Resp({"proposals": [], "matched": 0, "has_more": False, "returned": 0})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    cp.reset_canonical_cache()
    out = io.StringIO()
    args = NS(outbox=False, status=None, ai_id=None, output="json", since=None, limit=None, related=False)
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        rc = m.handle_mailbox_poll_command(
            args, _resolve_cortex_creds=lambda: ("https://c", "k"), _resolve_ai_id=lambda: "empirica-cortex"
        )
    return rc, json.loads(out.getvalue()), seen


def test_a_poll_whose_canonical_id_could_not_be_resolved_says_so_instead_of_reporting_zero_proposals(monkeypatch):
    rc, payload, seen = _poll(monkeypatch, roster_down=True)
    assert rc == 1
    assert payload["ok"] is False
    assert "canonical" in payload["error"].lower()
    assert not any("orchestration/inbox" in u for u in seen), "no inbox query may go out under the bare slug"


def test_with_the_roster_up_an_empty_inbox_is_still_a_clean_zero(monkeypatch):
    rc, payload, seen = _poll(monkeypatch, roster_down=False)
    assert rc == 0 and payload["ok"] is True and payload["count"] == 0
    assert payload["queried_as"] == CANON, "the envelope names the id the inbox was actually asked for"
    assert any(f"ai_id={CANON}" in u for u in seen)


ROSTER_WITHOUT_THE_CALLER = {
    "self": {"tenant_slug": "david"},
    "org": {
        "tenants": [
            {
                "tenant_slug": "david",
                "projects": [{"slug": "someone-else", "ai_id_mesh": "empirica.david.someone-else"}],
            }
        ]
    },
}


def test_a_slug_missing_from_a_reachable_roster_is_not_blamed_on_the_roster_being_down(monkeypatch):
    """The resolver answers None for an unreachable roster AND for a slug with no row in a reachable one, so the message said
    'the roster lookup failed ... retry when cortex is reachable' for a typo, the wrong cause and the wrong remedy."""
    rc, payload, seen = _poll(monkeypatch, roster_down=False, roster=ROSTER_WITHOUT_THE_CALLER)
    assert rc == 1 and payload["ok"] is False
    error = payload["error"].lower()
    assert "no row" in error and "unreachable" in error, "both causes must be named"
    assert "practice-context" in error, "the command that tells them apart must be named"
    assert not any("orchestration/inbox" in u for u in seen)

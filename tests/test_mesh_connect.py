"""`empirica mesh connect` takes a no-Cortex practice through sign-in, register and import (goal 7377658d).

Run against a fake Cortex that answers with the shapes cortex's own handlers return (create_artifact_import_handler, users/me,
projects/register), and a store built by SessionDatabase, so the columns are the real ones.
"""

from __future__ import annotations

import time
import uuid

import pytest

from empirica.core import mesh_connect as mc
from empirica.data.session_database import SessionDatabase

PROJECT = {"name": "alpha", "project_id": "11111111-2222-4333-8444-555555555555", "path": "/w/alpha"}


@pytest.fixture
def store(tmp_path, monkeypatch):
    db_path = tmp_path / "sessions.db"
    monkeypatch.setenv("EMPIRICA_SESSION_DB", str(db_path))
    db = SessionDatabase(db_path=str(db_path))
    sid = db.create_session(ai_id="t", project_id=PROJECT["project_id"])
    conn = db.conn
    ids = {}

    def add(table, kind, **cols):
        rid = str(uuid.uuid4())
        cols = {
            "id": rid,
            "project_id": PROJECT["project_id"],
            "session_id": sid,
            "created_timestamp": time.time(),
            **cols,
        }
        for _cid, name, ctype, notnull, default, _pk in conn.execute(f"PRAGMA table_info({table})").fetchall():
            if notnull and default is None and name not in cols:  # the store's required columns, filled plainly
                numeric = any(t in (ctype or "").upper() for t in ("INT", "REAL"))
                cols[name] = 0 if numeric else "{}"
        conn.execute(
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values())
        )
        ids[kind] = rid
        return rid

    add("project_findings", "finding", finding="the cache is cold on start", impact=0.6, visibility="shared")
    add("project_findings", "old_finding", finding="retracted claim", is_resolved=1, visibility="public")
    add("project_unknowns", "unknown", unknown="why is it cold?", visibility="local")
    add(
        "project_dead_ends",
        "dead_end",
        approach="warm it in a thread",
        why_failed="races the loader",
        visibility="local",
    )
    add("mistakes_made", "mistake", mistake="shipped the race", why_wrong="no test", prevention="add one")
    add("assumptions", "assumption", assumption="the loader is single-threaded", confidence=0.6, status="unverified")
    add("decisions", "decision", choice="warm on first use", rationale="no race", reversibility="exploratory")
    conn.execute(
        "INSERT INTO artifact_edges (from_id, to_id, relation, created_at) VALUES (?, ?, 'evidence', ?), (?, ?, 'related', ?),"
        " (?, ?, 'attached_to', ?)",
        (ids["decision"], ids["finding"], time.time(), ids["finding"], ids["unknown"], time.time(), ids["finding"], "goal-x", time.time()),
    )  # fmt: skip
    conn.commit()
    yield db, ids
    db.close()


class FakeCortex:
    def __init__(self, me=None, import_status=200, import_body=None):
        self.me = {"org_id": "org-1", "org_name": "Team One"} if me is None else me
        self.import_status, self.import_body = import_status, import_body
        self.calls: list[tuple] = []

    def __call__(self, method, url, bearer, body=None, timeout=30.0):
        self.calls.append((method, url.split("/v1/")[1], body))
        if url.endswith("/v1/users/me"):
            return (401, {}) if self.me == "401" else (200, self.me)
        if self.import_body is not None:
            return self.import_status, self.import_body
        n = len(body["nodes"])
        key = "would_create" if body["dry_run"] else "created"
        return 200, {
            "ok": True,
            "dry_run": body["dry_run"],
            key: n,
            "skipped_existing": 0,
            "refused": [],
            "edges_wired": len(body["edges"]),
        }

    def imports(self):
        return [c[2] for c in self.calls if c[1] == "artifacts/import"]


def _signed_in():
    return {"url": "https://cortex.test", "bearer": "tok", "source": "oauth"}


def _registered(project, url, bearer, timeout):
    return {"name": project["name"], "outcome": "registered", "status": 201, "project_id": project["project_id"]}


def test_a_practice_is_signed_in_registered_and_imported_with_its_ids_dates_and_links(store):
    db, ids = store
    cortex = FakeCortex()
    seen = []
    report = mc.connect(
        project=PROJECT, conn=db.conn, credential=_signed_in, http=cortex,
        register=lambda *a: seen.append(a) or _registered(*a), confirm_team=True,
    )  # fmt: skip
    assert report["ok"] is True and report["step"] == "done" and report["org"] == "Team One"
    assert seen and seen[0][1:3] == ("https://cortex.test", "tok")
    (body,) = cortex.imports()
    assert body["project_id"] == PROJECT["project_id"] and body["dry_run"] is False
    by_id = {n["id"]: n for n in body["nodes"]}
    assert set(ids.values()) <= set(by_id)
    f = by_id[ids["finding"]]
    assert f["ref"] == f["id"] and f["type"] == "finding" and isinstance(f["created_at"], float)
    assert f["data"]["finding"] == "the cache is cold on start" and f["data"]["visibility"] == "org"  # shared -> org
    assert by_id[ids["old_finding"]]["is_resolved"] is True and by_id[ids["old_finding"]]["data"]["visibility"] == "org"
    assert by_id[ids["unknown"]]["data"]["visibility"] == "project"  # local -> project
    dead = by_id[ids["dead_end"]]["data"]
    assert (dead["approach"], dead["why_failed"], dead["visibility"]) == (
        "warm it in a thread",
        "races the loader",
        "project",
    )
    assert report["public_as_org"] == 1
    assert report["to_team"] + report["to_project"] == len(body["nodes"]) and report["to_project"] >= 1
    assert body["edges"] == [{"from": ids["decision"], "to": ids["finding"], "relation": "evidence"}]
    assert report["links_kept_local"] == 2, "a 'related' link and a link to a goal stay local"
    assert report["created"] == len(body["nodes"]) and "Raw source files stay on this machine" in report["note"]


def test_no_team_stops_before_anything_is_written(store):
    db, _ = store
    cortex = FakeCortex(me={"org_id": "", "user_id": "u"})
    report = mc.connect(project=PROJECT, conn=db.conn, credential=_signed_in, http=cortex, register=pytest.fail)
    assert (report["ok"], report["step"]) == (False, "team") and report["error"] == mc.NO_TEAM
    assert cortex.imports() == []


@pytest.mark.parametrize(("cred", "me"), [({}, None), (_signed_in(), "401")])
def test_no_credential_or_a_rejected_one_stops_at_sign_in(store, cred, me):
    db, _ = store
    cortex = FakeCortex(me=me)
    report = mc.connect(project=PROJECT, conn=db.conn, credential=lambda: cred, http=cortex, register=pytest.fail)
    assert (report["ok"], report["step"]) == (False, "sign-in") and "auth login" in report["error"]
    assert cortex.imports() == []


def test_a_register_conflict_stops_before_the_import_and_says_why(store):
    db, _ = store
    cortex = FakeCortex()

    def taken(project, url, bearer, timeout):
        return {
            "name": "alpha",
            "outcome": "owner_conflict",
            "status": 409,
            "reason": "slug_taken: existing_id e-1, suggested alpha-2",
        }

    report = mc.connect(
        project=PROJECT, conn=db.conn, credential=_signed_in, http=cortex, register=taken, confirm_team=True
    )
    assert (report["ok"], report["step"]) == (False, "register") and "slug_taken" in report["error"]
    assert cortex.imports() == []


def test_an_import_cortex_refuses_is_reported_with_its_errors(store):
    db, _ = store
    cortex = FakeCortex(
        import_status=400, import_body={"ok": False, "errors": ["Node 'x': created_at is not an ISO-8601 time"]}
    )
    report = mc.connect(
        project=PROJECT, conn=db.conn, credential=_signed_in, http=cortex, register=_registered, confirm_team=True
    )
    assert (report["ok"], report["step"]) == (False, "import") and "created_at" in report["error"]


def test_refused_nodes_are_counted_and_the_rest_still_import(store):
    db, _ = store
    cortex = FakeCortex(
        import_body={"ok": True, "created": 5, "refused": ["n1", "n2"], "skipped_existing": 1, "edges_wired": 1}
    )
    report = mc.connect(
        project=PROJECT, conn=db.conn, credential=_signed_in, http=cortex, register=_registered, confirm_team=True
    )
    assert report["ok"] is True and (report["created"], report["refused"], report["skipped_existing"]) == (5, 2, 1)


def test_a_dry_run_registers_nothing_and_asks_cortex_what_it_would_write(store):
    db, _ = store
    cortex = FakeCortex()
    report = mc.connect(
        project=PROJECT, conn=db.conn, credential=_signed_in, http=cortex, register=pytest.fail, dry_run=True
    )
    (body,) = cortex.imports()
    assert body["dry_run"] is True and report["ok"] is True and report["would_create"] == len(body["nodes"])
    assert "register" not in report


def test_batches_hold_500_nodes_and_each_link_rides_with_its_later_end():
    nodes = [{"id": f"n{i:05d}", "ref": f"n{i:05d}"} for i in range(1001)]
    edges = [
        {"from": "n00000", "to": "n00900", "relation": "evidence"},
        {"from": "n00003", "to": "n00004", "relation": "evidence"},
    ]
    out = mc.batches(nodes, edges)
    assert [len(chunk) for chunk, _ in out] == [500, 500, 1]
    assert out[0][1] == [edges[1]] and out[1][1] == [edges[0]], "a link waits for the request that holds its later end"


def test_a_source_goes_without_its_file(store):
    db, _ = store
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(epistemic_sources)")}
    row = {"id": str(uuid.uuid4()), "project_id": PROJECT["project_id"], "source_type": "doc", "title": "RFC 7519",
           "source_url": "https://example.test/rfc", "canonical_path": "/home/u/private/rfc.pdf"}  # fmt: skip
    row = {k: v for k, v in row.items() if k in cols}
    for _cid, name, ctype, notnull, default, _pk in db.conn.execute("PRAGMA table_info(epistemic_sources)").fetchall():
        if notnull and default is None and name not in row:
            row[name] = 0 if any(t in (ctype or "").upper() for t in ("INT", "REAL")) else "x"
    db.conn.execute(
        f"INSERT INTO epistemic_sources ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values())
    )
    db.conn.commit()
    nodes, _ = mc.export_nodes(db.conn, PROJECT["project_id"])
    (src,) = [n for n in nodes if n["type"] == "source"]
    assert src["data"]["title"] == "RFC 7519" and "canonical_path" not in src["data"] and "/home/u" not in str(src)


def test_the_command_refuses_outside_a_practice_and_reads_the_practice_it_is_in(tmp_path, monkeypatch, capsys):
    import types

    from empirica.cli.command_handlers import mesh_commands
    from empirica.config import path_resolver

    monkeypatch.setattr(path_resolver, "get_git_root", lambda: tmp_path)
    args = types.SimpleNamespace(dry_run=True, output="json")
    assert mesh_commands.handle_mesh_connect_command(args) == 1
    assert "not inside a practice" in capsys.readouterr().out

    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(
        "ai_id: alpha\nproject_id: 11111111-2222-4333-8444-555555555555\n"
    )
    assert mc.practice_of(str(tmp_path)) == {
        "name": "alpha", "project_id": "11111111-2222-4333-8444-555555555555", "path": str(tmp_path)
    }  # fmt: skip


def test_nothing_goes_to_the_team_without_a_yes(store):
    """David, 2026-11-14: connect asks first; most local artifacts are 'shared' by the store's default."""
    db, _ = store
    cortex = FakeCortex()
    report = mc.connect(project=PROJECT, conn=db.conn, credential=_signed_in, http=cortex, register=pytest.fail)
    assert (report["ok"], report["step"]) == (False, "confirm") and "--yes" in report["error"]
    assert str(report["to_team"]) in report["error"] and cortex.imports() == []

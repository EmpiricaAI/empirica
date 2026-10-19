"""`empirica doctor --scrub-secrets`: remove credentials that were stored before write-time redaction existed.

Write-time redaction (test_artifact_text_redaction.py) stops new leaks; this removes old ones from the three places an artifact's
text lives: the SQLite row, the git note and the Qdrant payload. Dry-run by default, idempotent, and it never reaches into another
practice's collections (the gardening rule: only the practitioner inside a practice prunes it).
"""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from empirica.core import secret_scrub as ss

ADMIN = "ctx_" + "empirica_adm_" + "9f3a2b7c4d8e1f6a0b5c9d2e4a7b1c3d"
NTFY = "tk_" + "abcdefghijklmnopqrstuvwxyz0123456789"
PROSE = "bearer authentication is the scheme in use"


# --- SQLite ---------------------------------------------------------------------------------------------------------------


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("create table project_findings (id text primary key, finding text, finding_data text, impact real)")
    c.execute("create table project_dead_ends (id text primary key, approach text, why_failed text)")
    c.execute("create table unrelated (id text primary key, note text)")
    c.executemany(
        "insert into project_findings values (?,?,?,?)",
        [("f1", f"saw {ADMIN}", f'{{"finding": "saw {ADMIN}"}}', 0.5), ("f2", PROSE, f'{{"finding": "{PROSE}"}}', 0.4)],
    )
    c.execute("insert into project_dead_ends values ('d1', ?, ?)", (f"call with api_key {ADMIN}", f"401 using {NTFY}"))
    c.execute("insert into unrelated values ('u1', ?)", (f"not an artifact table {ADMIN}",))
    c.commit()
    return c


class TestSqlite:
    def test_dry_run_counts_and_changes_nothing(self, conn):
        report = ss.scrub_sqlite(conn, apply=False)
        assert report["rows"] == {"project_findings": 1, "project_dead_ends": 1}
        assert report["applied"] is False
        assert ADMIN in conn.execute("select finding from project_findings where id='f1'").fetchone()[0]

    def test_apply_scrubs_every_text_column_of_the_artifact_tables(self, conn):
        ss.scrub_sqlite(conn, apply=True)
        row = conn.execute("select finding, finding_data from project_findings where id='f1'").fetchone()
        assert ADMIN not in row[0] and ADMIN not in row[1] and "ctx_<redacted>" in row[0]
        d = conn.execute("select approach, why_failed from project_dead_ends").fetchone()
        assert ADMIN not in d[0] and NTFY not in d[1]

    def test_prose_and_non_text_columns_are_untouched(self, conn):
        ss.scrub_sqlite(conn, apply=True)
        assert conn.execute("select finding, impact from project_findings where id='f2'").fetchone() == (PROSE, 0.4)

    def test_tables_outside_the_artifact_set_are_never_touched(self, conn):
        ss.scrub_sqlite(conn, apply=True)
        assert ADMIN in conn.execute("select note from unrelated").fetchone()[0]

    def test_it_is_idempotent(self, conn):
        ss.scrub_sqlite(conn, apply=True)
        assert ss.scrub_sqlite(conn, apply=False)["rows"] == {}

    def test_a_missing_table_is_skipped_not_an_error(self):
        c = sqlite3.connect(":memory:")
        assert ss.scrub_sqlite(c, apply=True)["rows"] == {}


# --- Qdrant ---------------------------------------------------------------------------------------------------------------


class FakeQdrant:
    def __init__(self, data):
        self.data = data  # {collection: {point_id: payload}}
        self.writes = []

    def list_collections(self):
        return list(self.data)

    def scroll(self, collection):
        yield from list(self.data[collection].items())

    def set_payload(self, collection, point_id, payload):
        self.writes.append((collection, point_id, sorted(payload)))
        self.data[collection][point_id].update(payload)


@pytest.fixture
def qd():
    return FakeQdrant(
        {
            "project_mine_memory": {
                1: {"text": f"saw {ADMIN}", "type": "finding", "meta": {"note": f"also {NTFY}", "n": 3}},
                2: {"text": PROSE, "type": "finding"},
            },
            "project_mine_decisions": {7: {"choice": f"use {NTFY}"}},
            "project_other_memory": {9: {"text": f"someone else's {ADMIN}"}},
            "global_learnings": {5: {"text": f"promoted {ADMIN}"}},
            "epistemic_events": {6: {"data_json": f'{{"k": "{NTFY}"}}'}},
        }
    )


class TestQdrant:
    def test_dry_run_reports_per_collection_and_writes_nothing(self, qd):
        report = ss.scrub_qdrant(qd, "mine", include_global=False, apply=False)
        assert report["points"] == {"project_mine_memory": 1, "project_mine_decisions": 1}
        assert qd.writes == []

    def test_apply_scrubs_nested_strings_and_keeps_the_rest(self, qd):
        ss.scrub_qdrant(qd, "mine", include_global=False, apply=True)
        p = qd.data["project_mine_memory"][1]
        assert (
            ADMIN not in p["text"] and NTFY not in p["meta"]["note"] and p["meta"]["n"] == 3 and p["type"] == "finding"
        )
        assert qd.data["project_mine_memory"][2]["text"] == PROSE

    def test_only_changed_points_are_written(self, qd):
        ss.scrub_qdrant(qd, "mine", include_global=False, apply=True)
        assert sorted(w[1] for w in qd.writes) == [1, 7]

    def test_another_practices_collections_are_never_touched(self, qd):
        ss.scrub_qdrant(qd, "mine", include_global=True, apply=True)
        assert ADMIN in qd.data["project_other_memory"][9]["text"]

    def test_the_shared_collections_need_an_explicit_request(self, qd):
        ss.scrub_qdrant(qd, "mine", include_global=False, apply=True)
        assert ADMIN in qd.data["global_learnings"][5]["text"]
        report = ss.scrub_qdrant(qd, "mine", include_global=True, apply=True)
        assert (
            ADMIN not in qd.data["global_learnings"][5]["text"]
            and NTFY not in qd.data["epistemic_events"][6]["data_json"]
        )
        assert set(report["points"]) == {"global_learnings", "epistemic_events"}

    def test_it_is_idempotent(self, qd):
        ss.scrub_qdrant(qd, "mine", include_global=True, apply=True)
        writes = len(qd.writes)
        assert ss.scrub_qdrant(qd, "mine", include_global=True, apply=True)["points"] == {}
        assert len(qd.writes) == writes

    def test_an_unreachable_store_is_reported_not_raised(self):
        class Down:
            def list_collections(self):
                raise OSError("connection refused")

        report = ss.scrub_qdrant(Down(), "mine", include_global=True, apply=True)
        assert report["points"] == {} and "connection refused" in report["error"]


# --- git notes ------------------------------------------------------------------------------------------------------------


def _git(repo: Path, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, input=stdin, capture_output=True, text=True, check=True, timeout=30
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "T")
    (r / "a").write_text("x")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "one")
    (r / "b").write_text("y")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "two")
    first, second = _git(r, "rev-list", "--reverse", "HEAD").split()
    _git(r, "notes", "--ref=empirica/findings/f1", "add", "-F", "-", first, stdin=f'{{"finding": "saw {ADMIN}"}}')
    _git(r, "notes", "--ref=empirica/findings/f2", "add", "-F", "-", second, stdin=f'{{"finding": "{PROSE}"}}')
    return r


def _notes(repo: Path) -> dict[str, str]:
    out = {}
    for ref in _git(repo, "for-each-ref", "--format=%(refname)", "refs/notes/empirica").split():
        for line in _git(repo, "notes", f"--ref={ref.removeprefix('refs/notes/')}", "list").splitlines():
            note, obj = line.split()
            out[f"{ref}:{obj[:7]}"] = _git(repo, "cat-file", "-p", note)
    return out


class TestNotes:
    def test_dry_run_counts_and_changes_nothing(self, repo):
        before = _notes(repo)
        report = ss.scrub_notes(repo, apply=False)
        assert report["notes"] == 1 and _notes(repo) == before

    def test_apply_rewrites_only_the_note_that_held_a_credential(self, repo):
        before = _notes(repo)
        ss.scrub_notes(repo, apply=True)
        after = _notes(repo)
        assert len(after) == len(before) == 2
        assert not any(ADMIN in text for text in after.values())
        assert any("ctx_<redacted>" in text for text in after.values())
        assert any(PROSE in text for text in after.values())

    def test_it_is_idempotent(self, repo):
        ss.scrub_notes(repo, apply=True)
        assert ss.scrub_notes(repo, apply=False)["notes"] == 0

    def test_a_directory_that_is_not_a_repo_is_reported_not_raised(self, tmp_path):
        report = ss.scrub_notes(tmp_path, apply=True)
        assert report["notes"] == 0 and report.get("error")


# --- the doctor flag -------------------------------------------------------------------------------------------------------


class TestDoctorFlag:
    def _args(self, **kw):
        import types

        base = {
            "apply": False,
            "reconcile_notes": False,
            "scrub_secrets": True,
            "include_global": False,
            "deploy_gaps": False,
            "output": "json",
        }
        return types.SimpleNamespace(**{**base, **kw})

    @pytest.fixture
    def project(self, tmp_path, monkeypatch):
        (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
        (tmp_path / ".empirica" / "project.yaml").write_text("project_id: 11111111-2222-3333-4444-555555555555\n")
        db = sqlite3.connect(tmp_path / ".empirica" / "sessions" / "sessions.db")
        db.execute("create table project_findings (id text primary key, finding text)")
        db.execute("insert into project_findings values ('f1', ?)", (f"saw {ADMIN}",))
        db.commit()
        db.close()
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("EMPIRICA_QDRANT_URL", "http://127.0.0.1:1")  # nothing listens: the store is 'down'
        return tmp_path

    def test_the_flag_exists_and_apply_alone_is_still_refused(self, capsys):
        from empirica.cli.command_handlers import doctor

        assert doctor.handle_doctor_command(self._args(scrub_secrets=False, apply=True)) == 2

    def test_a_dry_run_reports_and_writes_nothing(self, project, capsys):
        import json

        from empirica.cli.command_handlers import doctor

        # Exit 2, not 0: a store that could not be read is not a clean store (the Qdrant here is down and the directory is not a
        # git repo). The sqlite part still ran and still reports what it found.
        assert doctor.handle_doctor_command(self._args()) == 2
        out = json.loads(capsys.readouterr().out)
        assert out["applied"] is False and out["sqlite"]["rows"] == {"project_findings": 1} and "--apply" in out["next"]
        assert out["qdrant"]["error"], "an unreachable Qdrant is reported, not raised"
        assert out["ok"] is False and {"qdrant", "notes"} <= set(out["incomplete"])
        row = (
            sqlite3.connect(project / ".empirica" / "sessions" / "sessions.db")
            .execute("select finding from project_findings")
            .fetchone()
        )
        assert ADMIN in row[0]

    def test_apply_scrubs_the_database(self, project, capsys):
        from empirica.cli.command_handlers import doctor

        assert doctor.handle_doctor_command(self._args(apply=True)) == 2, (
            "incomplete scan: the other stores were unreadable"
        )
        row = (
            sqlite3.connect(project / ".empirica" / "sessions" / "sessions.db")
            .execute("select finding from project_findings")
            .fetchone()
        )
        assert ADMIN not in row[0]


# --- the real HTTP client, against a server with Qdrant's actual payload semantics ----------------------------------------


class _QdrantServer:
    """POST .../points/payload MERGES the given keys; PUT .../points/payload REPLACES the whole payload (Qdrant's behaviour).
    Writing the scrubbed delta with PUT erased every other field of the point, which is how this was found."""

    def __init__(self):
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        self.points = {1: {"text": f"saw {ADMIN}", "meta": {"n": 3}, "type": "finding"}}
        outer = self

        class H(BaseHTTPRequestHandler):
            def _json(self, obj):
                body = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._json({"result": {"collections": [{"name": "project_mine_memory"}]}})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                if self.path.endswith("/points/scroll"):
                    pts = [{"id": i, "payload": p} for i, p in outer.points.items()]
                    return self._json({"result": {"points": pts, "next_page_offset": None}})
                if self.path.endswith("/points/payload"):
                    for pid in body["points"]:
                        outer.points[pid].update(body["payload"])
                    return self._json({"result": {"status": "acknowledged"}})
                self.send_error(404)

            def do_PUT(self):
                body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
                if self.path.endswith("/points/payload"):
                    for pid in body["points"]:
                        outer.points[pid] = dict(body["payload"])  # overwrite
                    return self._json({"result": {"status": "acknowledged"}})
                self.send_error(404)

            def log_message(self, *_a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class TestHttpClient:
    def test_scrubbing_a_point_keeps_every_other_field(self):
        server = _QdrantServer()
        try:
            report = ss.scrub_qdrant(ss.QdrantHttp(server.url), "mine", include_global=False, apply=True)
            assert report["points"] == {"project_mine_memory": 1} and not report.get("error")
            payload = server.points[1]
            assert ADMIN not in payload["text"] and "ctx_<redacted>" in payload["text"]
            assert payload["meta"] == {"n": 3} and payload["type"] == "finding", (
                "the update replaced the payload instead of merging"
            )
        finally:
            server.close()

    def test_a_dry_run_sends_no_write(self):
        server = _QdrantServer()
        try:
            ss.scrub_qdrant(ss.QdrantHttp(server.url), "mine", include_global=False, apply=False)
            assert ADMIN in server.points[1]["text"]
        finally:
            server.close()

"""Credentials never reach stored artifact text, and ordinary prose is left alone.

cortex found live admin-key text in seven practices' Qdrant memory collections and in the shared global_learnings store
(2026-10-14). core's redactor existed since July but was called only by the PostToolUseFailure hook, so a key typed or pasted into
`finding-log`, `deadend-log`, a batch node or any direct repository call went straight to SQLite, git notes and Qdrant. The writers
now redact at every entry point. The storage variant is stricter about prose than the hook's: "bearer authentication" and
"token budget_exhausted" are knowledge, not credentials, and must survive.
"""

from __future__ import annotations

import io
import json
import types
import uuid

import pytest

from empirica.core import redaction

# Assembled from fragments so no secret-shaped literal sits in the repo (GitHub push protection flags them).
ADMIN = "ctx_" + "empirica_adm_" + "9f3a2b7c4d8e1f6a0b5c9d2e4a7b1c3d"
NTFY = "tk_" + "abcdefghijklmnopqrstuvwxyz0123456789"
JWT = "eyJ" + "hbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxMjM0NTY3ODkw" + "." + "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV"
SK = "sk-" + "proj-" + "aBcD3fGh1jKlMnOpQrStUvWxYz012345"

PROJECT = str(uuid.uuid4())
SESSION = str(uuid.uuid4())


class TestStorageRedactor:
    @pytest.mark.parametrize(
        "text, secret",
        [
            (f"call mcp__cortex__cortex_inbox_poll with api_key {ADMIN} failed", ADMIN),
            (f"curl -H 'Authorization: Bearer {NTFY}' https://ntfy.example/x", NTFY),
            (f"the listener used {NTFY} directly", NTFY),
            (f"Authorization: token {'0123456789abcdef' * 2}", "0123456789abcdef" * 2),
            (f"session jwt {JWT} expired", JWT),
            (f"key {SK} leaked", SK),
            ("clone https://user:hunter2hunter2@forge.example/x.git", "hunter2hunter2"),
            ('{"api_key": "plainpassword-value"}', "plainpassword-value"),
        ],
    )
    def test_a_credential_shape_is_redacted(self, text, secret):
        out = redaction.redact_for_storage(text)
        assert secret not in out and "<redacted>" in out

    def test_the_label_that_says_which_credential_survives(self):
        out = redaction.redact_for_storage(f"key {ADMIN} and {NTFY}")
        assert "ctx_<redacted>" in out and "tk_<redacted>" in out

    @pytest.mark.parametrize(
        "prose",
        [
            "bearer authentication is the scheme in use",
            "the token estimation heuristic was wrong",
            "token budget_exhausted flagged on the third retry",
            "use a bearer credential_rotation policy",
            "the api key rotation is pending for next week",
            "Authorization headers are logged by the proxy",
            "ctx is a short name and ctx_adm alone is not a key",
            "a tk_ prefix is how ntfy names its access tokens",
            "see https://example.org/docs/a_long_path_segment_that_is_not_a_secret",
            "12345678901234567890 is just a number",
            # code and documentation that MENTION credentials without containing one (found in this practice's own store):
            'headers={"Authorization": f"Bearer {token}"} is how the request is built',
            "{'Authorization': 'Bearer ' + token, 'Accept': 'application/json'}",
            "API_KEY=$(grep -E '^CORTEX_API_KEY' file | awk '{print $2}') then curl",
            "clone https://oauth2:${FORGEJO_TOKEN}@git.example/x.git then push",
            "remote add origin https://user:<token>@host/repo.git",
            "remote url https://user:token@host/repo.git",
            "keys look like ctx_empirica_adm_<32 hex> and the prefix ctx_empirica_adm_ is not a key",
            "revoke every ctx_*_adm_* key that appears in a collection",
            "a STALE CORTEX_API_KEY (ctx_empirica_adm_...) — revoked",
        ],
    )
    def test_ordinary_prose_is_unchanged(self, prose):
        assert redaction.redact_for_storage(prose) == prose

    def test_the_hook_style_redactor_would_have_mangled_that_prose(self):
        """The reason a separate storage variant exists: the broad rules rewrite knowledge."""
        assert (
            redaction.redact_secrets("bearer authentication is the scheme in use")
            != "bearer authentication is the scheme in use"
        )

    def test_it_never_raises_and_fails_closed(self, monkeypatch):
        assert redaction.redact_for_storage(None) is None and redaction.redact_for_storage("") == ""
        monkeypatch.setattr(redaction, "_STORAGE_SHAPES", None)
        assert redaction.redact_for_storage(f"x {ADMIN}") == redaction.REDACTED

    def test_the_shared_shape_list_now_knows_the_ntfy_token(self):
        assert NTFY not in redaction.redact_secrets(f"the listener used {NTFY} directly")


class TestFieldHelper:
    def test_known_text_fields_are_scrubbed_and_others_left_alone(self):
        data = {
            "finding": f"saw {ADMIN}",
            "impact": 0.7,
            "subject": "ops",
            "description": f"## notes\n{NTFY}",
            "goal_id": "g1",
        }
        out = redaction.redact_artifact_fields(data)
        assert ADMIN not in out["finding"] and NTFY not in out["description"]
        assert out["impact"] == 0.7 and out["subject"] == "ops" and out["goal_id"] == "g1"
        assert ADMIN in data["finding"], "the input mapping must not be mutated"

    def test_non_string_values_pass_through(self):
        assert redaction.redact_artifact_fields({"finding": None, "approach": 5}) == {"finding": None, "approach": 5}


@pytest.fixture
def breadcrumbs(tmp_path):
    from empirica.data.session_database import SessionDatabase

    db = SessionDatabase(db_path=str(tmp_path / "redact.db"))
    yield db.breadcrumbs
    db.close()


def _all_text(breadcrumbs) -> str:
    conn = breadcrumbs.conn
    parts = []
    for table in (
        "project_findings",
        "project_unknowns",
        "project_dead_ends",
        "mistakes_made",
        "assumptions",
        "decisions",
    ):
        for row in conn.execute(f"select * from {table}").fetchall():
            parts.append(" ".join(str(v) for v in tuple(row) if v is not None))
    return "\n".join(parts)


class TestRepositoryBackstop:
    def test_every_artifact_writer_scrubs_its_text(self, breadcrumbs):
        b = breadcrumbs
        b.log_finding(PROJECT, SESSION, f"found {ADMIN} in a log", description=f"body {NTFY}")
        b.log_unknown(PROJECT, SESSION, f"is {ADMIN} still live")
        b.log_dead_end(PROJECT, SESSION, f"mcp call with api_key {ADMIN}", f"401 for {NTFY}")
        b.log_mistake(
            session_id=SESSION,
            project_id=PROJECT,
            mistake=f"pasted {ADMIN}",
            why_wrong=f"why {NTFY}",
            prevention=f"prevent {SK}",
        )
        b.log_assumption(
            project_id=PROJECT, session_id=SESSION, assumption=f"assume {ADMIN} is rotated", confidence=0.5
        )
        b.log_decision(
            project_id=PROJECT,
            session_id=SESSION,
            choice=f"use {ADMIN}",
            rationale=f"because {NTFY}",
            reversibility="exploratory",
        )

        stored = _all_text(b)
        for secret in (ADMIN, NTFY, SK):
            assert secret not in stored, f"{secret[:6]}... reached the store"
        assert "ctx_<redacted>" in stored and "tk_<redacted>" in stored

    def test_prose_is_stored_as_written(self, breadcrumbs):
        breadcrumbs.log_finding(PROJECT, SESSION, "bearer authentication is the scheme in use")
        assert "bearer authentication is the scheme in use" in _all_text(breadcrumbs)

    def test_the_same_secret_text_twice_still_dedupes(self, breadcrumbs):
        a = breadcrumbs.log_finding(PROJECT, SESSION, f"found {ADMIN} here")
        b = breadcrumbs.log_finding(PROJECT, SESSION, f"found {ADMIN} here")
        assert a == b


class TestCliFunnel:
    def test_config_from_stdin_is_scrubbed_before_a_handler_reads_it(self, monkeypatch):
        from empirica.cli.command_handlers import artifact_log_commands as alc

        payload = json.dumps({"approach": f"tried {ADMIN}", "why_failed": f"401 {NTFY}", "impact": 0.4})
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        cfg = alc._parse_config_input(types.SimpleNamespace(config="-"))
        assert ADMIN not in cfg["approach"] and NTFY not in cfg["why_failed"] and cfg["impact"] == 0.4

    def test_flag_values_are_scrubbed_in_place(self):
        from empirica.cli.command_handlers import artifact_log_commands as alc

        args = types.SimpleNamespace(config=None, finding=f"saw {ADMIN}", description=f"d {NTFY}", impact=0.5)
        assert alc._parse_config_input(args) is None
        assert ADMIN not in args.finding and NTFY not in args.description and args.impact == 0.5

    def test_the_mistake_handler_scrubs_too(self):
        from empirica.cli.command_handlers import artifact_log_commands as alc

        args = types.SimpleNamespace(
            mistake=f"pasted {ADMIN}", why_wrong=f"w {NTFY}", prevention=f"p {SK}", config=None
        )
        alc._redact_artifact_inputs(args, None)
        assert ADMIN not in args.mistake and NTFY not in args.why_wrong and SK not in args.prevention


class TestBatchNodes:
    def test_node_data_is_scrubbed_before_it_reaches_notes_or_embeddings(self):
        from empirica.cli.command_handlers import graph_commands as gc

        node = {"type": "finding", "ref": "f1", "data": {"finding": f"saw {ADMIN}", "impact": 0.6}}
        out = gc._redact_node(node)
        assert ADMIN not in out["data"]["finding"] and out["data"]["impact"] == 0.6

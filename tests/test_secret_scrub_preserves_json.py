"""The offline scrub must never turn valid JSON into invalid JSON.

Found by an independent sweep of v1.14.11..HEAD (finding 1): the storage redactor's flag rule `--api-key <value>` took `\\S{8,}`
as the value, so when the value was the last word of a JSON string it also swallowed the closing quote and comma. The write-time
path scrubs plain strings before they are encoded, so it was unaffected, but `doctor --scrub-secrets --apply` runs the redactor
over the raw text of git notes and of `*_data` / `goal_data` columns and writes the result back. A note that stops parsing is
lost to a rebuild from notes, and a column that stops parsing is dropped silently by any reader that does
`json.loads(...) except: {}`.

Two layers, each tested on its own: the flag rule stops before a quote or delimiter, and JSON text is scrubbed STRUCTURALLY so a
future rule cannot reintroduce the break.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess

import pytest

from empirica.core import secret_scrub as ss
from empirica.core.redaction import redact_for_storage

KEY = "abcdef123456"
SECRET_WORD = "--api-key " + KEY


@pytest.mark.parametrize("tail", ['"', '",', '"}', '"]', "'", "',", ")", ");", "]", "}", ",", ";"])
def test_the_flag_rule_stops_before_a_closing_delimiter(tail):
    out = redact_for_storage(f"ran with {SECRET_WORD}{tail}")
    assert KEY not in out, "the value is still redacted"
    assert out.endswith(tail), f"the delimiter {tail!r} must survive: {out!r}"


def test_a_bare_flag_value_at_end_of_text_is_still_redacted():
    assert KEY not in redact_for_storage(f"ran with {SECRET_WORD}")


def test_a_flag_with_equals_is_still_redacted():
    out = redact_for_storage("curl --api-key=" + KEY + ' "https://x"')
    assert KEY not in out and out.endswith('"https://x"')


def test_ordinary_prose_is_untouched():
    text = "the --api-key flag is documented in the CLI reference"
    assert redact_for_storage(text) == text


# --- structural scrub --------------------------------------------------------------------------------------------------


def test_json_text_is_scrubbed_structurally_and_stays_valid():
    note = json.dumps({"finding": f"ran with {SECRET_WORD}", "tags": ["a"], "n": 3}, indent=2)
    out = ss.scrub_text(note)
    parsed = json.loads(out)
    assert KEY not in out
    assert parsed["tags"] == ["a"] and parsed["n"] == 3, "everything but the secret survives"


def test_json_without_a_secret_is_returned_byte_for_byte():
    note = json.dumps({"finding": "plain", "tags": ["a"]}, indent=2)
    assert ss.scrub_text(note) == note


def test_non_json_text_goes_through_the_plain_redactor():
    assert KEY not in ss.scrub_text(f"call {SECRET_WORD} now")


def test_a_json_scalar_is_not_mistaken_for_a_document():
    assert ss.scrub_text("123") == "123"


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("create table project_findings (id text primary key, finding text, finding_data text, impact real)")
    c.execute(
        "insert into project_findings values ('f1', ?, ?, 0.5)",
        (f"ran with {SECRET_WORD}", json.dumps({"finding": f"ran with {SECRET_WORD}", "tags": ["a"]})),
    )
    c.commit()
    return c


def test_sqlite_json_columns_stay_valid_json_after_apply(conn):
    ss.scrub_sqlite(conn, apply=True)
    blob = conn.execute("select finding_data from project_findings").fetchone()[0]
    assert json.loads(blob)["tags"] == ["a"]
    assert KEY not in blob


@pytest.fixture
def repo(tmp_path):
    p = tmp_path / "r"
    p.mkdir()

    def run(*a):
        return subprocess.run(["git", *a], cwd=p, capture_output=True, text=True, check=True)

    run("init", "-q")
    run("config", "user.email", "t@t")
    run("config", "user.name", "t")
    (p / "f").write_text("x")
    run("add", ".")
    run("commit", "-qm", "i")
    return p


def test_git_notes_stay_valid_json_after_apply(repo):
    payload = json.dumps({"finding": f"ran with {SECRET_WORD}", "tags": ["a"]}, indent=2)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    subprocess.run(
        ["git", "notes", "--ref=empirica/findings/f1", "add", "-f", "-F", "-", head],
        cwd=repo,
        input=payload,
        text=True,
        capture_output=True,
        check=True,
    )
    report = ss.scrub_notes(repo, apply=True)
    assert report["notes"] == 1 and "error" not in report
    listing = subprocess.run(
        ["git", "notes", "--ref=empirica/findings/f1", "list"], cwd=repo, capture_output=True, text=True
    ).stdout.split()
    body = subprocess.run(["git", "cat-file", "-p", listing[0]], cwd=repo, capture_output=True, text=True).stdout
    assert json.loads(body)["tags"] == ["a"], body
    assert KEY not in body
    assert ss.scrub_notes(repo, apply=False)["notes"] == 0, "idempotent"

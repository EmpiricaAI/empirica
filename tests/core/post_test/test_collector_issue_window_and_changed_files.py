"""Two collector paths that could never report what they were written to report.

D. The issues source filtered auto_captured_issues by transaction_id /
   created_timestamp, columns that table does not have (it has created_at). The
   OperationalError was swallowed into an empty result on every POSTFLIGHT.
E. With nothing changed since PREFLIGHT, the changed-files lookup fell back to
   HEAD~5..HEAD, so inside any repo with history the INSUFFICIENT profile was
   unreachable and the last five commits were graded as this transaction's work.
"""

from __future__ import annotations

import logging
import sqlite3
import subprocess
import time
from datetime import datetime, timezone

from empirica.core.post_test.collector import EvidenceProfile, PostTestCollector

_ISSUES_DDL = """
CREATE TABLE auto_captured_issues (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, severity TEXT NOT NULL, category TEXT NOT NULL,
    code_location TEXT, message TEXT NOT NULL, stack_trace TEXT, context TEXT, status TEXT DEFAULT 'new',
    assigned_to_ai TEXT, root_cause_id TEXT, resolution TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


class _DB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(_ISSUES_DDL)

    def close(self):
        self.conn.close()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_an_issue_inside_the_window_produces_issue_density_evidence():
    db = _DB()
    start = time.time() - 60
    # issue_capture writes created_at as a UTC isoformat string
    db.conn.execute(
        "INSERT INTO auto_captured_issues (id, session_id, severity, category, message, status, created_at)"
        " VALUES ('i1', 's1', 'high', 'error', 'boom', 'new', ?)",
        (_iso(start + 30),),
    )
    # before the window: must not be counted
    db.conn.execute(
        "INSERT INTO auto_captured_issues (id, session_id, severity, category, message, status, created_at)"
        " VALUES ('i0', 's1', 'low', 'error', 'old', 'resolved', ?)",
        (_iso(start - 3600),),
    )
    c = PostTestCollector(session_id="s1", db=db, transaction_id="tx-1", preflight_timestamp=start)
    items = {i.metric_name: i for i in c._collect_issue_metrics()}
    assert "inverse_severe_issue_density" in items
    assert items["inverse_severe_issue_density"].raw_value == {"severe": 1, "total": 1}
    assert items["issue_resolution_ratio"].raw_value["total"] == 1


def test_a_failing_issue_query_is_logged_at_warning(caplog):
    db = _DB()
    db.conn.execute("DROP TABLE auto_captured_issues")
    c = PostTestCollector(session_id="s1", db=db, preflight_timestamp=time.time())
    with caplog.at_level(logging.WARNING, logger="empirica.core.post_test.collector"):
        assert c._collect_issue_metrics() == []
    assert any("auto_captured_issues" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def _git(repo, *args, env=None):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        env=env,
    )


def test_a_repo_with_history_and_no_changes_since_preflight_is_insufficient(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("EMPIRICA_EVIDENCE_PROFILE", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    old = {"GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z"}
    import os

    env = {**os.environ, **old}
    for i in range(6):
        (repo / f"m{i}.py").write_text(f"x = {i}\n")
        _git(repo, "add", ".", env=env)
        _git(repo, "commit", "-q", "-m", f"c{i}", env=env)

    db = _DB()
    db.conn.execute("CREATE TABLE sessions (session_id TEXT, start_time TEXT)")
    c = PostTestCollector(session_id="s1", db=db, preflight_timestamp=time.time())
    c._project_root = str(repo)
    assert c._get_session_changed_files() == []
    assert c._resolve_profile() == EvidenceProfile.INSUFFICIENT

    # Positive control: an edit since PREFLIGHT is seen
    (repo / "m0.py").write_text("x = 99\n")
    c2 = PostTestCollector(session_id="s1", db=db, preflight_timestamp=time.time())
    c2._project_root = str(repo)
    assert c2._get_session_changed_files() == ["m0.py"]


def test_a_transaction_that_only_creates_untracked_files_lists_them(tmp_path, monkeypatch):
    """Without the HEAD~5 fallback, a new file nobody has `git add`ed was in none of log/diff/diff --cached, so the
    transaction's code was not graded. Untracked files written before the window, and ignored ones, stay out."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    import os

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    old = {"GIT_AUTHOR_DATE": "2020-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2020-01-01T00:00:00Z"}
    (repo / ".gitignore").write_text("*.log\n")
    (repo / "base.py").write_text("x = 1\n")
    _git(repo, "add", ".", env={**os.environ, **old})
    _git(repo, "commit", "-q", "-m", "base", env={**os.environ, **old})
    # A real leftover is old in ctime too (utime cannot backdate ctime, and the filter reads both since a moved or
    # copied file keeps an old mtime), so the window opens after the leftover is written.
    (repo / "leftover.py").write_text("y = 0\n")  # untracked, but from before this transaction
    time.sleep(1.1)  # the window is read in whole seconds
    start = time.time()
    time.sleep(1.1)
    (repo / "new.py").write_text("z = 2\n")
    (repo / "debug.log").write_text("ignored\n")

    db = _DB()
    db.conn.execute("CREATE TABLE sessions (session_id TEXT, start_time TEXT)")
    c = PostTestCollector(session_id="s1", db=db, preflight_timestamp=start)
    c._project_root = str(repo)
    assert c._get_session_changed_files() == ["new.py"]


def test_a_file_moved_in_during_the_transaction_counts_despite_its_old_mtime(tmp_path):
    """Second review pass: mv / cp -a keep an old mtime; the change time says when it arrived."""
    import os

    f = tmp_path / "moved.py"
    f.write_text("a = 1\n")
    os.utime(f, (1_000_000_000, 1_000_000_000))  # an old mtime, as cp -a keeps; ctime is now
    st = f.stat()
    assert max(st.st_mtime, st.st_ctime) >= time.time() - 60

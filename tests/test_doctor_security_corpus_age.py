"""``empirica doctor`` says when the security corpus has not been re-verified for a long time.

The corpus is shipped reference material and nothing refreshes it automatically (an earlier README promised a monthly loop that
never existed), so the only way it stays current is a human re-verifying a file and bumping its ``**Verified:**`` date. The
check reads those dates; it never fails the build, because a calendar flip must not turn CI red on its own.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from empirica.cli.command_handlers import doctor

NOW = datetime(2026, 10, 15, 12, 0).timestamp()


def _file(directory: Path, name: str, verified: str | None) -> None:
    body = f"# {name}\n\n**Canonical source:** https://example.org/{name}\n**Edition:** test\n"
    if verified is not None:
        body += f"**Verified:** {verified} against https://example.org/{name}\n"
    (directory / name).write_text(body + "\nbody\n")


@pytest.fixture
def corpus(tmp_path: Path) -> Path:
    d = tmp_path / "corpus"
    d.mkdir()
    (d / "README.md").write_text("# readme without a Verified line\n")
    return d


def test_recent_files_pass_and_the_oldest_is_named(corpus):
    _file(corpus, "a.md", "2026-10-15")
    _file(corpus, "b.md", "2026-08-01")

    check = doctor.check_security_corpus(corpus, now=NOW)

    assert check.status == doctor.PASS
    assert "b.md" in check.detail and "75" in check.detail


def test_a_file_older_than_the_limit_warns_and_names_the_file(corpus):
    _file(corpus, "a.md", "2026-10-01")
    _file(corpus, "old.md", "2026-01-01")

    check = doctor.check_security_corpus(corpus, now=NOW)

    assert check.status == doctor.WARN
    assert "old.md" in check.detail and "a.md" not in check.detail
    assert "re-verify" in (check.hint or "")


def test_exactly_at_the_limit_still_passes(corpus):
    _file(corpus, "edge.md", "2026-04-18")  # 180 days before 2026-10-15

    assert doctor.check_security_corpus(corpus, now=NOW).status == doctor.PASS


def test_a_file_with_no_verified_date_warns_instead_of_counting_as_fresh(corpus):
    _file(corpus, "fresh.md", "2026-10-15")
    _file(corpus, "undated.md", None)

    check = doctor.check_security_corpus(corpus, now=NOW)

    assert check.status == doctor.WARN and "undated.md" in check.detail and "no Verified date" in check.detail


def test_a_garbled_date_is_not_read_as_fresh(corpus):
    _file(corpus, "bad.md", "2026-13-45")

    check = doctor.check_security_corpus(corpus, now=NOW)

    assert check.status == doctor.WARN and "bad.md" in check.detail


def test_a_missing_directory_warns_because_absent_is_not_healthy(tmp_path):
    check = doctor.check_security_corpus(tmp_path / "nowhere", now=NOW)

    assert check.status == doctor.WARN and "not found" in check.detail


def test_an_empty_directory_warns(corpus):
    assert doctor.check_security_corpus(corpus, now=NOW).status == doctor.WARN


def test_the_readme_is_not_a_corpus_file(corpus):
    _file(corpus, "a.md", "2026-10-15")

    check = doctor.check_security_corpus(corpus, now=NOW)

    assert check.status == doctor.PASS and "README" not in check.detail

"""Crontab lines are persisted by the scanner too, so they are redacted like process command lines (finding 6 of the release sweep).

10b9de0ad redacted process command lines and made the snapshot files owner-only, but `_read_crontab` still stored each line
verbatim. A cron job that calls an API with `Authorization: Bearer tk_...` or `API_KEY=...` therefore landed in the snapshot,
now at mode 0600 but still plaintext.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from empirica.core.scanner import scheduled

TOKEN = "tk_" + "abcdefghijklmnopqrstuvwxyz0123456789"
SK = "sk-" + "abcdefghijklmnopqrstuvwxyz0123456789"


def _crontab(text: str):
    return subprocess.CompletedProcess(args=["crontab", "-l"], returncode=0, stdout=text, stderr="")


def _rows(text: str):
    with patch("subprocess.run", return_value=_crontab(text)), patch("shutil.which", return_value="/usr/bin/crontab"):
        return scheduled._read_crontab()


def test_a_bearer_token_in_a_cron_line_is_redacted():
    rows = _rows(f'*/5 * * * * curl -s -H "Authorization: Bearer {TOKEN}" https://x.example/ping\n')
    assert len(rows) == 1 and TOKEN not in rows[0]["line"]
    assert "curl" in rows[0]["line"], "the rest of the line is kept: it is what makes the row useful"


def test_an_assignment_style_key_in_a_cron_line_is_redacted():
    rows = _rows(f"0 3 * * * API_KEY={SK} /usr/local/bin/backup.sh\n")
    assert SK not in rows[0]["line"] and "backup.sh" in rows[0]["line"]


def test_an_ordinary_cron_line_is_unchanged_and_comments_are_still_skipped():
    rows = _rows("# a comment\n\n17 3 * * * /usr/bin/true\n")
    assert [r["line"] for r in rows] == ["17 3 * * * /usr/bin/true"]

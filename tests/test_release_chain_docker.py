"""release_chain's docker probe must tell an ABSENT tag from a registry that could not be asked.

``docker manifest inspect`` exits non-zero both when the tag does not exist and when Docker Hub rate-limits, times out or the
daemon is unreachable. The probe mapped every non-zero exit to "missing", so a transient registry error read as "release not
published" in the report whose job is to say whether a release shipped. Two compliance runs on 2026-10-19 reported
``docker: missing`` for a tag that ``docker manifest inspect`` found seconds later. PyPI already had the tri-state
(``check_failed``); this gives docker the same.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from empirica.cli.command_handlers.compliance_report_commands import _check_channel


def _run(returncode: int, stderr: str = ""):
    return subprocess.CompletedProcess(args=["docker"], returncode=returncode, stdout="", stderr=stderr)


def _status(result):
    with patch("subprocess.run", return_value=result):
        return _check_channel("docker", "1.14.11", Path("."))


def test_a_found_manifest_is_published():
    assert _status(_run(0)) == "published"


@pytest.mark.parametrize(
    "stderr",
    [
        "no such manifest: docker.io/nubaeon/empirica:9.9.9",
        "manifest unknown: manifest unknown",
        "Error: not found",
    ],
)
def test_a_tag_the_registry_says_does_not_exist_is_missing(stderr):
    assert _status(_run(1, stderr)) == "missing"


@pytest.mark.parametrize(
    "stderr",
    [
        "toomanyrequests: You have reached your unauthenticated pull rate limit",
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
        'Get "https://registry-1.docker.io/v2/": net/http: request canceled (Client.Timeout exceeded)',
        "",
    ],
)
def test_a_probe_that_could_not_be_answered_is_check_failed_not_missing(stderr):
    assert _status(_run(1, stderr)) == "check_failed"


def test_a_timeout_is_check_failed():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="docker", timeout=15)):
        assert _check_channel("docker", "1.14.11", Path(".")) == "check_failed"


def test_docker_not_installed_is_check_failed():
    with patch("subprocess.run", side_effect=FileNotFoundError("docker")):
        assert _check_channel("docker", "1.14.11", Path(".")) == "check_failed"

"""release_chain PyPI check queries the live API, not pip's local cache.

Regression: `_check_channel("pypi", ...)` used `pip index versions`, which reads
pip's HTTP cache — stale for minutes after a fresh publish — so `release_chain`
reported pypi "missing" right after `release.py --publish` even though the
version was live. `_pypi_has_version` now hits PyPI's JSON API directly.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from empirica.cli.command_handlers.compliance_report_commands import _pypi_has_version


def _fake_response(payload: dict):
    """A urlopen() context-manager stand-in whose .read() returns `payload`."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def test_true_when_version_present():
    with patch("urllib.request.urlopen", return_value=_fake_response({"releases": {"1.12.30": [], "1.12.29": []}})):
        assert _pypi_has_version("empirica", "1.12.30") is True


def test_false_when_version_absent():
    with patch("urllib.request.urlopen", return_value=_fake_response({"releases": {"1.12.29": []}})):
        assert _pypi_has_version("empirica", "1.12.30") is False


def test_false_on_network_error():
    # A transient failure must not crash the compliance report — just "missing".
    with patch("urllib.request.urlopen", side_effect=OSError("network down")):
        assert _pypi_has_version("empirica", "1.12.30") is False


def test_false_on_malformed_json():
    resp = MagicMock()
    resp.read.return_value = b"not json"
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    with patch("urllib.request.urlopen", return_value=cm):
        assert _pypi_has_version("empirica", "1.12.30") is False

"""Notes are refused on a public host, judged by the remote's host, not a substring (goal d897e750, pilot sweep).

The refusal used provider detection, a substring classifier: codeberg.org (public) read as forgejo and passed, and a remote
whose URL could not be read was "unknown" and passed (harmless: git cannot push to it either).
"""

from __future__ import annotations

import pytest

from empirica.cli.command_handlers import sync_commands as sc


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo.git",
        "git@github.com:org/repo.git",
        "ssh://git@gitlab.com/org/repo.git",
        "https://codeberg.org/org/repo.git",
        "https://bitbucket.org/org/repo.git",
        "https://user@GitHub.com/org/repo",
    ],
)
def test_public_hosts_are_public(url):
    assert sc._public_host(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://git.getempirica.com/org/repo.git",  # our private Forgejo
        "https://forgejo.example/github.com-mirror.git",  # the public name in the path, not the host
        "https://github.company.internal/org/repo.git",  # an enterprise host, not github.com
        "git@gitlab.internal:org/repo.git",
        "/srv/git/repo.git",  # a local path
    ],
)
def test_near_misses_are_not_public(url):
    assert not sc._public_host(url)

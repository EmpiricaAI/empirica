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


# ── `visibility: public` declares a remote public that the host list cannot know (goal d897e750) ──────────────────────
# The key was stored, validated and printed, and nothing read it.


def _refused(monkeypatch, capsys, url, visibility):
    monkeypatch.setattr(sc, "_get_remote_url", lambda remote: url)
    monkeypatch.setattr(sc, "_list_remotes", lambda: ["origin"])
    monkeypatch.setattr(sc, "_load_sync_config", lambda: {**sc.DEFAULT_SYNC_CONFIG, "visibility": visibility})
    refused = sc._refuse_public_notes_remote("origin", force=False, output_format="json")
    return refused, capsys.readouterr().out


def test_a_remote_declared_public_is_refused_whatever_its_host(monkeypatch, capsys):
    refused, out = _refused(monkeypatch, capsys, "https://git.example.org/team/repo.git", "public")
    assert refused and "visibility" in out


def test_a_private_declaration_does_not_unlock_a_public_host(monkeypatch, capsys):
    refused, _ = _refused(monkeypatch, capsys, "https://github.com/org/repo.git", "private")
    assert refused


def test_a_private_self_hosted_remote_still_pushes(monkeypatch, capsys):
    refused, _ = _refused(monkeypatch, capsys, "https://git.example.org/team/repo.git", "private")
    assert not refused


def test_force_overrides_the_declaration_too(monkeypatch):
    monkeypatch.setattr(sc, "_get_remote_url", lambda remote: "https://git.example.org/r.git")
    monkeypatch.setattr(sc, "_load_sync_config", lambda: {**sc.DEFAULT_SYNC_CONFIG, "visibility": "public"})
    assert not sc._refuse_public_notes_remote("origin", force=True, output_format="json")


@pytest.mark.parametrize(
    "url", ["git@github.com-work:org/r.git", "git@gitlab.com_personal:org/r.git", "ssh://git@codeberg.org-alt/o/r.git"]
)
def test_an_ssh_alias_of_a_public_host_is_public(url):
    # A common ~/.ssh/config pattern names a second key's host 'github.com-work'; the old substring check caught it, the
    # host check let it through (pre-publish sweep B2, goal 4c935e3b).
    assert sc._public_host(url)


def test_a_private_host_that_merely_starts_with_a_public_name_is_not_public():
    assert not sc._public_host("git@github.company.internal:org/r.git")
    assert not sc._public_host("https://gitlab.common-corp.example/org/r.git")

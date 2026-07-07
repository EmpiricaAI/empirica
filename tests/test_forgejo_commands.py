"""Tests for `empirica forgejo-publish` (forgejo_commands).

All cortex HTTP + git subprocess calls are mocked, so these run on any host.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from empirica.cli.command_handlers import forgejo_commands as fc

_UUID = "a0e24049-d159-4834-afcb-930ba64d0e2b"
_FORGEJO_URL = "https://git.getempirica.com/david/empirica-mesh-support.git"
_TOKEN = "tok_sha1secret"  # noqa: S105 — fake test fixture, not a real secret
_TOKEN_USER = "proj-a0e24049"  # noqa: S105 — fake test fixture
_REFSPECS = [
    "+refs/heads/*:refs/heads/*",
    "+refs/tags/*:refs/tags/*",
    "+refs/notes/empirica/*:refs/notes/empirica/*",
]


def _proc(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(
        yaml.safe_dump({"project_id": _UUID, "name": "empirica-mesh-support"})
    )
    return root


# ── _resolve_project / _resolve_cortex_config ───────────────────────────


def test_resolve_project_reads_yaml(tmp_path):
    root = _make_project(tmp_path)
    pid, name = fc._resolve_project(root)
    assert pid == _UUID
    assert name == "empirica-mesh-support"


def test_resolve_project_missing(tmp_path):
    assert fc._resolve_project(tmp_path / "nope") == (None, None)


# ── _write_token / _compose_push_url ────────────────────────────────────


def test_write_token_0600(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    p = fc._write_token(_UUID, _TOKEN)
    assert p.read_text() == _TOKEN
    assert (p.stat().st_mode & 0o777) == 0o600
    assert p.parent == tmp_path / ".config" / "empirica" / "forgejo-tokens"


def test_compose_push_url_inserts_creds():
    assert fc._compose_push_url(_FORGEJO_URL, _TOKEN_USER, _TOKEN) == (
        f"https://{_TOKEN_USER}:{_TOKEN}@git.getempirica.com/david/empirica-mesh-support.git"
    )


def test_compose_push_url_url_encodes_special_chars():
    out = fc._compose_push_url("https://h/r.git", "u@x", "t/s+e")
    assert out == "https://u%40x:t%2Fs%2Be@h/r.git"


def test_compose_push_url_passes_through_non_https():
    assert fc._compose_push_url("ssh://x/y.git", "u", "t") == "ssh://x/y.git"


# ── _set_forgejo_remote: add vs set-url, never touches origin ────────────


def test_set_forgejo_remote_adds_when_absent(tmp_path):
    calls = []

    def fake_git(path, *args, **kw):
        calls.append(args)
        return _proc(stdout="origin\n")  # only origin exists

    with patch.object(fc, "_git", fake_git):
        fc._set_forgejo_remote(tmp_path, _FORGEJO_URL)

    assert ("remote",) in calls  # listed first
    assert ("remote", "add", "forgejo", _FORGEJO_URL) in calls


def test_set_forgejo_remote_updates_when_present(tmp_path):
    calls = []

    def fake_git(path, *args, **kw):
        calls.append(args)
        return _proc(stdout="origin\nforgejo\n")

    with patch.object(fc, "_git", fake_git):
        fc._set_forgejo_remote(tmp_path, _FORGEJO_URL)

    assert ("remote", "set-url", "forgejo", _FORGEJO_URL) in calls
    assert ("remote", "add", "forgejo", _FORGEJO_URL) not in calls


def test_set_forgejo_remote_path_scopes_credential_store(tmp_path):
    """Bug #2 fix: must set credential.useHttpPath=true (repo-local) so each
    forgejo repo's per-project token gets its own ~/.git-credentials entry
    instead of colliding under a shared host-keyed store."""
    calls = []

    def fake_git(path, *args, **kw):
        calls.append(args)
        return _proc(stdout="origin\n")

    with patch.object(fc, "_git", fake_git):
        fc._set_forgejo_remote(tmp_path, _FORGEJO_URL)

    assert ("config", "--local", "credential.useHttpPath", "true") in calls


# ── handle_forgejo_publish_command ──────────────────────────────────────


def test_publish_happy_path_pushes_all_refspecs(tmp_path, monkeypatch, capsys):
    root = _make_project(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    pushed = []

    def fake_push_refspec(pp, url, spec):
        pushed.append((url, spec))
        return True, ""

    with (
        patch.object(fc, "_resolve_cortex_config", lambda: ("https://cortex.example", "ctx_k")),
        patch.object(
            fc,
            "_forgejo_publish_post",
            lambda *a, **k: (
                200,
                {
                    "forgejo_repo_url": _FORGEJO_URL,
                    "forgejo_token": _TOKEN,
                    "forgejo_token_user": _TOKEN_USER,
                    "refspecs": _REFSPECS,
                },
            ),
        ),
        patch.object(fc, "_set_forgejo_remote", lambda *a, **k: None),
        patch.object(fc, "_push_refspec", fake_push_refspec),
    ):
        args = SimpleNamespace(path=str(root), output="json", rotate=False, description=None)
        code = fc.handle_forgejo_publish_command(args)

    assert code == 0
    # all three refspecs pushed, each to the credentialed composed URL (not a remote name)
    assert {spec for _, spec in pushed} == set(_REFSPECS)
    assert all(url.startswith(f"https://{_TOKEN_USER}:{_TOKEN}@") for url, _ in pushed)


# ── notes-ref chunking (avoid gateway 504 on large note volumes) ────────


def test_is_notes_wildcard():
    assert fc._is_notes_wildcard("+refs/notes/empirica/*:refs/notes/empirica/*")
    assert fc._is_notes_wildcard("refs/notes/empirica/*:refs/notes/empirica/*")
    assert not fc._is_notes_wildcard("+refs/heads/*:refs/heads/*")
    assert not fc._is_notes_wildcard("+refs/tags/*:refs/tags/*")


def test_push_refspec_non_notes_is_single_push(tmp_path):
    calls = []

    def fake_git(path, *args, **kw):
        calls.append(args)
        return _proc(returncode=0)

    with patch.object(fc, "_git", fake_git):
        ok, _ = fc._push_refspec(tmp_path, "URL", "+refs/heads/*:refs/heads/*")
    assert ok is True
    assert calls == [("push", "URL", "+refs/heads/*:refs/heads/*")]


def test_push_refspec_notes_chunked_into_batches(tmp_path):
    refs = "\n".join(f"refs/notes/empirica/findings/{i}" for i in range(600))
    push_batches = []

    def fake_git(path, *args, **kw):
        if args and args[0] == "for-each-ref":
            return _proc(stdout=refs + "\n")
        if args and args[0] == "push":
            push_batches.append(args[2:])  # explicit refspecs after (push, URL)
            return _proc(returncode=0)
        return _proc(returncode=0)

    with patch.object(fc, "_git", fake_git):
        ok, _ = fc._push_refspec(tmp_path, "URL", "+refs/notes/empirica/*:refs/notes/empirica/*")
    assert ok is True
    assert len(push_batches) == 3  # 250 + 250 + 100
    assert len(push_batches[0]) == 250
    assert len(push_batches[2]) == 100
    # explicit force refspecs, never the wildcard
    assert push_batches[0][0] == "+refs/notes/empirica/findings/0:refs/notes/empirica/findings/0"


def test_push_refspec_notes_empty_is_benign(tmp_path):
    def fake_git(path, *args, **kw):
        if args and args[0] == "for-each-ref":
            return _proc(stdout="")
        return _proc(returncode=0)

    with patch.object(fc, "_git", fake_git):
        ok, _ = fc._push_refspec(tmp_path, "URL", "+refs/notes/empirica/*:refs/notes/empirica/*")
    assert ok is True  # nothing to mirror is not a failure


def test_publish_already_published_no_key_is_ok_with_note(tmp_path, monkeypatch):
    root = _make_project(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch.object(fc, "_resolve_cortex_config", lambda: ("https://cortex.example", "ctx_k")),
        patch.object(
            fc,
            "_forgejo_publish_post",
            lambda *a, **k: (
                200,
                {
                    "forgejo_repo_url": _FORGEJO_URL,
                    "already_published": True,
                },
            ),
        ),
    ):
        args = SimpleNamespace(path=str(root), output="json", rotate=False, description=None)
        code = fc.handle_forgejo_publish_command(args)
    assert code == 0  # idempotent re-call is not an error


def test_publish_already_published_human_output_warns_not_success(tmp_path, monkeypatch, capsys):
    """Bug #1 fix: an already-published re-call (no token → no push) must NOT
    print a bare '✅ Forgejo provisioned' — that's the false-success. It warns
    that nothing was pushed and points at --rotate."""
    root = _make_project(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch.object(fc, "_resolve_cortex_config", lambda: ("https://cortex.example", "ctx_k")),
        patch.object(
            fc,
            "_forgejo_publish_post",
            lambda *a, **k: (200, {"forgejo_repo_url": _FORGEJO_URL, "already_published": True}),
        ),
    ):
        args = SimpleNamespace(path=str(root), output="human", rotate=False, description=None)
        code = fc.handle_forgejo_publish_command(args)
    out = capsys.readouterr().out
    assert code == 0
    assert "✅ Forgejo provisioned" not in out  # no false success
    assert "⚠️" in out and "nothing pushed" in out
    assert "--rotate" in out


def test_publish_cortex_error_returns_nonzero(tmp_path, monkeypatch):
    root = _make_project(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    with (
        patch.object(fc, "_resolve_cortex_config", lambda: ("https://cortex.example", "ctx_k")),
        patch.object(fc, "_forgejo_publish_post", lambda *a, **k: (403, {"error": "not owner"})),
    ):
        args = SimpleNamespace(path=str(root), output="json", rotate=False, description=None)
        code = fc.handle_forgejo_publish_command(args)
    assert code == 1


def test_publish_no_project_yaml_returns_nonzero(tmp_path):
    args = SimpleNamespace(path=str(tmp_path / "empty"), output="json", rotate=False, description=None)
    code = fc.handle_forgejo_publish_command(args)
    assert code == 1

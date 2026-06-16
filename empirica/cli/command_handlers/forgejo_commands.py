"""`empirica forgejo-publish` — provision a managed Forgejo remote for a project.

This is the operator / self-hosting-power-user provisioning verb (NOT an
end-user default flow). It drives the PUSH provisioning mode: Forgejo becomes a
git remote for the project and the local repo is pushed up. That's the right
mode for a project with **no existing remote** (e.g. empirica-mesh-support) —
Forgejo's managed pull-mirror can't apply when there's no origin to pull from.

Flow (cortex G12 HTTPS+token contract):
  1. POST /v1/projects/{id}/forgejo-publish (cortex, owner-scoped) → returns
     {forgejo_repo_url (https), forgejo_token (once), forgejo_token_user, refspecs[], ...}
  2. Stash forgejo_token 0600 to ~/.config/empirica/forgejo-tokens/<uuid>
  3. `git remote add forgejo <clean-https-url>` (or set-url if it already exists)
  4. Push each refspec to https://<user>:<token>@host/... composed at push-time
     (over :443; the credentialed URL is never persisted)

repo_url stays pinned to `origin`: adding the `forgejo` remote must NOT become
the project's canonical repo_url (that's live-read from origin elsewhere).
"""

from __future__ import annotations

import json
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

FORGEJO_PUBLISH_PATH = "/v1/projects/{project_id}/forgejo-publish"
FORGEJO_REMOTE_NAME = "forgejo"


def _resolve_cortex_config() -> tuple[str | None, str | None]:
    """(cortex_url, api_key) from ~/.empirica/credentials.yaml `cortex.*`."""
    try:
        cred = Path.home() / ".empirica" / "credentials.yaml"
        if not cred.exists():
            return None, None
        cfg = yaml.safe_load(cred.read_text()) or {}
        cortex = cfg.get("cortex") or {}
        return cortex.get("url"), cortex.get("api_key")
    except Exception:
        return None, None


def _resolve_project(path: Path) -> tuple[str | None, str | None]:
    """(project_id, name) from <path>/.empirica/project.yaml. (None, None) on miss."""
    try:
        py = path / ".empirica" / "project.yaml"
        if not py.exists():
            return None, None
        cfg = yaml.safe_load(py.read_text()) or {}
        return cfg.get("project_id"), cfg.get("name") or cfg.get("ai_id")
    except Exception:
        return None, None


def _forgejo_publish_post(
    cortex_url: str, project_id: str, api_key: str, *,
    rotate: bool = False, description: str | None = None, timeout: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    """POST the forgejo-publish endpoint. Returns (status, parsed_body)."""
    url = cortex_url.rstrip("/") + FORGEJO_PUBLISH_PATH.format(project_id=project_id)
    body: dict[str, Any] = {}
    if rotate:
        body["rotate"] = True
    if description:
        body["description"] = description
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {"error": str(e)}
    except Exception as e:
        return -1, {"error": f"{type(e).__name__}: {e}"}


def _write_token(project_uuid: str, token: str) -> Path:
    """Stash the Forgejo access token 0600 under ~/.config/empirica/forgejo-tokens/<uuid>.

    Enables offline-replayable pushes (re-push without a fresh cortex round-trip).
    Only the bare token is stored — the credential-composed push URL is NEVER
    persisted.
    """
    tok_dir = Path.home() / ".config" / "empirica" / "forgejo-tokens"
    tok_dir.mkdir(parents=True, exist_ok=True)
    tok_path = tok_dir / project_uuid
    tok_path.write_text(token)
    tok_path.chmod(0o600)
    return tok_path


def _compose_push_url(repo_url: str, token_user: str, token: str) -> str:
    """Insert credentials into an https repo URL for an ephemeral push.

    Returns https://<user>:<token>@host/path. Used ONLY as a push-time argument;
    never written to git config or persisted (the `forgejo` remote keeps the
    credential-free URL).
    """
    from urllib.parse import quote
    if not repo_url.startswith("https://"):
        return repo_url
    rest = repo_url[len("https://"):]
    return f"https://{quote(token_user, safe='')}:{quote(token, safe='')}@{rest}"


def _git(project_path: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """Run git in `project_path` (HTTPS push carries creds in the URL arg)."""
    return subprocess.run(
        ["git", "-C", str(project_path), *args],
        capture_output=True, text=True, timeout=timeout, check=False,
    )


def _set_forgejo_remote(project_path: Path, url: str) -> None:
    """Add the `forgejo` remote, or update its URL if it already exists.

    Never touches `origin` — repo_url stays pinned to the canonical remote.
    """
    existing = _git(project_path, "remote")
    remotes = (existing.stdout or "").split()
    if FORGEJO_REMOTE_NAME in remotes:
        _git(project_path, "remote", "set-url", FORGEJO_REMOTE_NAME, url)
    else:
        _git(project_path, "remote", "add", FORGEJO_REMOTE_NAME, url)


NOTES_PUSH_BATCH = 250  # refs per RPC — pushing thousands of note refs in one
#                         RPC times out at the gateway (HTTP 504).


def _is_notes_wildcard(spec: str) -> bool:
    """A `+refs/notes/...*:...` wildcard refspec (can match thousands of refs)."""
    src = spec.split(":", 1)[0].lstrip("+").strip()
    return src.startswith("refs/notes/") and src.endswith("*")


def _push_refspec(project_path: Path, push_url: str, spec: str) -> tuple[bool, str]:
    """Push one refspec. A notes wildcard is enumerated and pushed in batches —
    a single RPC with thousands of note refs 504s at the gateway. Non-notes
    refspecs (heads/tags) push in one shot. Returns (ok, last_stderr).
    """
    if not _is_notes_wildcard(spec):
        r = _git(project_path, "push", push_url, spec)
        return r.returncode == 0, (r.stderr or "")

    base = spec.split(":", 1)[0].lstrip("+").strip()[:-1]  # drop trailing '*'
    force_pfx = "+" if spec.lstrip().startswith("+") else ""
    listing = _git(project_path, "for-each-ref", "--format=%(refname)", base)
    refs = [r for r in (listing.stdout or "").splitlines() if r.strip()]
    if not refs:
        return True, ""  # nothing to mirror — benign
    ok, last_err = True, ""
    for i in range(0, len(refs), NOTES_PUSH_BATCH):
        batch = refs[i:i + NOTES_PUSH_BATCH]
        r = _git(project_path, "push", push_url, *(f"{force_pfx}{ref}:{ref}" for ref in batch))
        if r.returncode != 0:
            ok = False
            last_err = r.stderr or last_err
    return ok, last_err


def handle_forgejo_publish_command(args) -> int:
    """Provision a managed Forgejo remote + push the project to it."""
    output_json = getattr(args, "output", "human") == "json"
    project_path = Path(getattr(args, "path", ".") or ".").resolve()

    def _emit(payload: dict[str, Any], code: int) -> int:
        if output_json:
            print(json.dumps(payload, indent=2))
        else:
            if payload.get("ok"):
                print(f"✅ Forgejo provisioned: {payload.get('forgejo_repo_url')}")
                for ref, ok in (payload.get("push_results") or {}).items():
                    print(f"   {'✓' if ok else '✗'} {ref}")
                if payload.get("token_path"):
                    print(f"   token: {payload['token_path']} (0600)")
            else:
                print(f"❌ forgejo-publish: {payload.get('error') or payload.get('reason')}")
        return code

    project_id, _ = _resolve_project(project_path)
    if not project_id:
        return _emit({"ok": False, "reason": f"no .empirica/project.yaml at {project_path}"}, 1)

    cortex_url, api_key = _resolve_cortex_config()
    if not (cortex_url and api_key):
        return _emit({"ok": False, "reason": "no cortex url/api_key (configure ~/.empirica/credentials.yaml)"}, 1)

    status, body = _forgejo_publish_post(
        cortex_url, project_id, api_key,
        rotate=getattr(args, "rotate", False),
        description=getattr(args, "description", None),
    )
    if status != 200:
        return _emit({"ok": False, "reason": f"cortex {status}", "error": body.get("error"), "body": body}, 1)

    forgejo_url = body.get("forgejo_repo_url")
    refspecs = body.get("refspecs") or []
    token = body.get("forgejo_token")
    token_user = body.get("forgejo_token_user")
    if not forgejo_url:
        return _emit({"ok": False, "reason": "cortex response missing forgejo_repo_url", "body": body}, 1)

    # Idempotent re-call without rotate returns no token (unrecoverable) — can't push.
    if not (token and token_user):
        return _emit({
            "ok": True, "forgejo_repo_url": forgejo_url,
            "already_published": bool(body.get("already_published")),
            "note": "no token returned (already published) — re-run with --rotate to mint a fresh token and re-push",
        }, 0)

    tok_path = _write_token(project_id, token)
    _set_forgejo_remote(project_path, forgejo_url)  # clean URL — credentials never persisted
    push_url = _compose_push_url(forgejo_url, token_user, token)

    push_results: dict[str, bool] = {}
    for spec in refspecs:
        # Push to the credentialed URL directly so the `forgejo` remote + git
        # config stay secret-free (the composed form is never persisted). Notes
        # wildcards are chunked to stay under the gateway timeout.
        ok, err = _push_refspec(project_path, push_url, spec)
        push_results[spec] = ok
        if not ok and not output_json:
            # Scrub the token from any error echo.
            print(f"   ✗ push {spec}: {(err or '').strip().replace(token, '***')[:200]}")

    ok = all(push_results.values()) if push_results else True
    return _emit({
        "ok": ok,
        "forgejo_repo_url": forgejo_url,
        "refspecs": refspecs,
        "push_results": push_results,
        "token_path": str(tok_path),
    }, 0 if ok else 1)

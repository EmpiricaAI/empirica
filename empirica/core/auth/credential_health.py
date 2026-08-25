"""A 401 that cannot be fixed by retrying must stop the retry. Measured: 25h, ~10k requests.

A bare 401 is indistinguishable from a transient failure, so a client with a dead
credential retries forever. Cortex now closes that gap on the wire — every 401 body
carries two fields saying whether retrying can EVER work:

    {"error": "...", "credential_status": S, "retry": R}

    S in missing_credential | invalid_key | expired_token | invalid_token
    R in refresh | reauthenticate

`expired_token` -> `refresh` is the ONLY retry-with-hope state. Everything else
means stop and re-authenticate. (`invalid_key` deliberately collapses
unknown-vs-revoked: identical client action, and splitting them would build a
key-validity oracle.)

This module is core's side of that contract: remember which credential is dead so
the NEXT process does not re-storm.

**Persistence is the whole point.** The CLI is short-lived — a process-local cache
would be empty on every invocation and every invocation would re-discover the death
by making the request. So the verdict lands on disk.

**The escape path stays open, and that is the load-bearing guard.** A death mark is
cleared the moment `credentials.yaml` changes, so `empirica auth login` un-brickes
the seat by writing the file, with no separate reset step to discover. A guard whose
clear-path is itself gated is how you turn a recoverable outage into a permanent one.

**Fingerprints, never secrets.** A credential is identified here by a truncated
SHA-256. This practice already has a live admin key sitting verbatim in eleven of
its own artifacts; a health file holding another copy would be that mistake with a
new filename.

**Absent fields fail OPEN.** A server that has not shipped the contract yet, or a
proxy that ate the body, produces no verdict and nothing is marked. Bricking auth
because a field was missing would be a worse failure than the storm.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The one status where retrying has any hope; everything else needs a human.
REFRESHABLE = "expired_token"
#: Statuses that mean: stop, this credential will never work again as-is.
TERMINAL = frozenset({"missing_credential", "invalid_key", "invalid_token"})

_HEALTH_FILE = "credential_health.json"


def _empirica_dir() -> Path:
    return Path.home() / ".empirica"


def _health_path() -> Path:
    return _empirica_dir() / _HEALTH_FILE


def _credentials_path() -> Path:
    return _empirica_dir() / "credentials.yaml"


def fingerprint(secret: str | None) -> str | None:
    """Stable non-reversible id for a credential. Never the credential itself."""
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:16]


def parse_unauthorized(body: Any, headers: dict[str, str] | None = None) -> dict[str, str] | None:
    """Extract ``{credential_status, retry}`` from a 401, or None if absent.

    Accepts the parsed dict, or raw text/bytes it will try to parse — callers hold
    the response in whatever shape their transport gave them, and making each one
    normalise first is how a field gets honoured on two paths out of five.

    Returns None when the fields are not present. That is the FAIL-OPEN case and it
    is deliberate: no verdict means no action, never a default of `dead`.
    """
    data: Any = body
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    if isinstance(body, str):
        try:
            data = json.loads(body)
        except Exception:
            data = None
    if not isinstance(data, dict):
        # RFC 6750 puts the machine-readable reason in WWW-Authenticate when the
        # body is empty, which is the shape the OAuth-challenge paths use.
        auth = (headers or {}).get("WWW-Authenticate") or (headers or {}).get("www-authenticate")
        if auth and "invalid_token" in auth:
            return {"credential_status": "invalid_token", "retry": "reauthenticate"}
        return None

    status = data.get("credential_status")
    if not isinstance(status, str) or not status:
        return None
    retry = data.get("retry")
    if not isinstance(retry, str) or not retry:
        # Derive rather than discard: the retry field is a function of the status,
        # so a body carrying one and not the other is still actionable.
        retry = "refresh" if status == REFRESHABLE else "reauthenticate"
    return {"credential_status": status, "retry": retry}


def _read() -> dict[str, Any]:
    """Load the health file, dropping it if credentials have changed since.

    The mtime comparison IS the escape path: writing `credentials.yaml` — which is
    what `auth login` and the extension bridge both do — invalidates every death
    mark. There is no reset command to discover, because a seat that is bricked and
    told to run a command it cannot find is bricked twice.
    """
    try:
        raw = json.loads(_health_path().read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    try:
        creds_mtime = _credentials_path().stat().st_mtime
    except OSError:
        creds_mtime = None
    if creds_mtime is not None and float(raw.get("credentials_mtime") or 0.0) < creds_mtime:
        logger.debug("credential_health: credentials.yaml changed, clearing death marks")
        return {}
    return raw


def _write(state: dict[str, Any]) -> None:
    try:
        _empirica_dir().mkdir(parents=True, exist_ok=True)
        try:
            state["credentials_mtime"] = _credentials_path().stat().st_mtime
        except OSError:
            state["credentials_mtime"] = 0.0
        _health_path().write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        # Non-fatal: losing the mark costs a re-storm, raising costs the request.
        logger.debug(f"credential_health: could not persist ({e})")


def mark(secret: str | None, verdict: dict[str, str] | None) -> None:
    """Record a terminal verdict against a credential. No-op for anything else."""
    fp = fingerprint(secret)
    if not fp or not verdict:
        return
    if verdict.get("credential_status") not in TERMINAL:
        return  # refreshable, or a status we do not recognise — not our business
    state = _read()
    marks = state.setdefault("dead", {})
    marks[fp] = {
        "credential_status": verdict.get("credential_status"),
        "retry": verdict.get("retry"),
        "at": time.time(),
    }
    _write(state)
    logger.warning(
        f"credential marked unusable ({verdict.get('credential_status')}): "
        f"re-authenticate — `empirica auth login`. Further requests with it are suppressed."
    )


def dead_reason(secret: str | None) -> str | None:
    """The recorded terminal status for this credential, or None if usable.

    Returns the REASON rather than a boolean on purpose: a caller that suppresses a
    credential has to be able to say why it did, or the suppression is
    indistinguishable from the credential being absent — which is the same
    unfalsifiable-silence this whole contract exists to remove.
    """
    fp = fingerprint(secret)
    if not fp:
        return None
    entry = (_read().get("dead") or {}).get(fp)
    if not isinstance(entry, dict):
        return None
    return entry.get("credential_status") or "unusable"


def clear() -> None:
    """Forget every mark. For `auth login` and for tests."""
    try:
        _health_path().unlink()
    except OSError:
        pass

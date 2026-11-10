"""One message for a capability the server does not have.

A server that lacks a capability answers ``501 {"capability": "<name>"}`` (the Cortex contract; empirica-local does so
for everything that crosses a user or organisation boundary). Every client call site that can meet one says the same
thing through these helpers, instead of each failing its own way ("HTTP 501", "unavailable_http_501", a bare dict).
"""

from __future__ import annotations

import json
import urllib.error
from typing import Any, overload

PREFIX = "not available on this server: "


def unavailable_message(status: int, body: Any) -> str | None:
    """``"not available on this server: <capability>"`` for a 501 naming one; None for anything else."""
    if status != 501:
        return None
    if isinstance(body, (bytes, str)):
        try:
            body = json.loads(body)
        except ValueError:
            return None
    capability = body.get("capability") if isinstance(body, dict) else None
    return f"{PREFIX}{capability}" if capability else None


def unavailable_from_error(e: urllib.error.HTTPError) -> str | None:
    """The message for an ``HTTPError``, reading its body once and caching it on the error for later readers."""
    if e.code != 501:
        return None
    raw = getattr(e, "_empirica_body", None)
    if raw is None:
        try:
            raw = e.read()
        except OSError:
            raw = b""
        e._empirica_body = raw  # type: ignore[attr-defined]
    return unavailable_message(e.code, raw)


@overload
def annotate_unavailable(status: int, body: dict) -> dict: ...
@overload
def annotate_unavailable(status: int, body: None) -> None: ...
def annotate_unavailable(status: int, body: dict | None) -> dict | None:
    """Put the message under ``error`` of an already-parsed reply body, so callers that print ``error`` say it."""
    message = unavailable_message(status, body)
    if message and isinstance(body, dict):
        body["error"] = message
    return body

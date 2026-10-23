"""Empirica CLI invocation helpers for chat artifact actions (Phase 4).

Slash commands in chat (/finding, /decision, /unknown) call these to
create real Empirica artifacts via the existing CLI. The returned
artifact_id flows into the rendered ArtifactCard so its action buttons
can later resolve/discuss/pin against the real artifact.

Subprocess pattern (same as cockpit_commands uses for privileged tools):
cheaper to maintain than direct module imports — no internal API churn
risk, no Python import-graph entanglement with the rest of empirica.

Phase 4 supports: finding, decision, unknown. Phase 4b adds: mistake,
dead_end, assumption, source. Goals + transactions remain CLI-only for v1.
"""

from __future__ import annotations

import json
import subprocess
from typing import Any


class ActionError(RuntimeError):
    """Empirica CLI returned non-zero or unparseable JSON."""


def _run_cli(args: list[str], timeout: float = 10.0) -> dict[str, Any]:
    """Invoke `empirica <args>` with --output json appended, return parsed dict."""
    cmd = ["empirica", *args, "--output", "json"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise ActionError(f"empirica CLI timeout: {' '.join(cmd[:3])}") from e
    except FileNotFoundError as e:
        raise ActionError("empirica CLI not on PATH") from e

    if result.returncode != 0:
        raise ActionError(f"empirica CLI exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise ActionError(f"empirica CLI returned non-JSON: {result.stdout[:200]}") from e


def log_finding(text: str, impact: float = 0.5, subject: str | None = None) -> dict[str, Any]:
    """Create a finding artifact. Returns the parsed JSON response."""
    args = ["finding-log", "--finding", text, "--impact", str(impact)]
    if subject:
        args.extend(["--subject", subject])
    return _run_cli(args)


def log_decision(choice: str, rationale: str = "", reversibility: str = "exploratory") -> dict[str, Any]:
    """Create a decision artifact. Returns the parsed JSON response."""
    args = ["decision-log", "--choice", choice, "--reversibility", reversibility]
    if rationale:
        args.extend(["--rationale", rationale])
    return _run_cli(args)


def log_unknown(text: str, subject: str | None = None) -> dict[str, Any]:
    """Create an unknown artifact. Returns the parsed JSON response."""
    args = ["unknown-log", "--unknown", text]
    if subject:
        args.extend(["--subject", subject])
    return _run_cli(args)


def resolve_unknown(unknown_id: str, resolved_by: str | None = None) -> dict[str, Any]:
    """Resolve a single unknown via `empirica unknown-resolve`.

    Phase 4b — wired into ArtifactCard's resolve button so users can
    close unknowns directly from the chat conversation. `resolved_by`
    is an optional human-readable note about how it was resolved.
    """
    args = ["unknown-resolve", "--unknown-id", unknown_id]
    if resolved_by:
        args.extend(["--resolved-by", resolved_by])
    return _run_cli(args)


def log_artifacts_from_file(path: str) -> dict[str, Any]:
    """Run `empirica log-artifacts -` with payload piped from a JSON file.

    Phase 11 batch-create wrapper. The empirica CLI accepts a graph
    payload {nodes: [...], edges: [...]} via stdin. We read the file
    here so the chat slash handler can pass it through unchanged.

    Returns the CLI's parsed JSON response (created/errors/edges_wired).
    Raises ActionError on file-read or CLI failures.
    """
    import contextlib

    try:
        with open(path, encoding="utf-8") as f:
            payload = f.read()
    except OSError as e:
        raise ActionError(f"failed to read batch payload from {path}: {e}") from e
    if not payload.strip():
        raise ActionError(f"batch payload at {path} is empty")
    cmd = ["empirica", "log-artifacts", "-", "--output", "json"]
    try:
        result = subprocess.run(
            cmd,
            input=payload,
            capture_output=True,
            text=True,
            timeout=30.0,
        )
    except subprocess.TimeoutExpired as e:
        raise ActionError("empirica log-artifacts batch timeout") from e
    except FileNotFoundError as e:
        raise ActionError("empirica CLI not on PATH") from e
    if result.returncode != 0:
        raise ActionError(
            f"empirica log-artifacts batch exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        # Some batch responses may not be JSON if there's stderr noise — surface
        # the stdout as-is so the user can see what happened.
        with contextlib.suppress(Exception):
            return {"raw_output": result.stdout, "warning": f"non-JSON response: {e}"}
        raise ActionError(f"unparseable batch response: {result.stdout[:200]}") from e


#: Types `delete-artifacts` takes by id from a chat line. Lessons are excluded on purpose: the verb refuses to delete a wrong
#: lesson (it is superseded), and a chat one-liner is the wrong place to learn that.
DELETABLE_TYPES = ("finding", "unknown", "dead_end", "mistake", "assumption", "decision")


def parse_artifact_refs(tokens: list[str]) -> list[tuple[str, str]]:
    """``["finding:abc", ...]`` -> ``[("finding", "abc"), ...]``. delete-artifacts needs the TYPE with every id; an id alone
    names no table."""
    refs: list[tuple[str, str]] = []
    for token in tokens:
        kind, sep, ident = token.partition(":")
        if not sep or not ident or kind not in DELETABLE_TYPES:
            raise ActionError(f"expected TYPE:ID with TYPE one of {', '.join(DELETABLE_TYPES)}; got {token!r}")
        refs.append((kind, ident))
    return refs


def _run_batch_verb(verb: str, payload: dict[str, Any], extra_args: list[str] | None = None) -> dict[str, Any]:
    cmd = ["empirica", verb, "-", *(extra_args or []), "--output", "json"]
    try:
        result = subprocess.run(cmd, input=json.dumps(payload), capture_output=True, text=True, timeout=15.0)
    except subprocess.TimeoutExpired as e:
        raise ActionError(f"empirica {verb} batch timeout") from e
    except FileNotFoundError as e:
        raise ActionError("empirica CLI not on PATH") from e
    if result.returncode != 0:
        raise ActionError(
            f"empirica {verb} batch exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw_output": result.stdout}


def resolve_artifacts_batch(ids: list[str], resolution: str = "resolved from chat") -> dict[str, Any]:
    """Run `empirica resolve-artifacts -` over a list of UNKNOWN ids.

    The payload is the verb's own shape (``resolutions``: one ``{type, id, resolution}`` each). It used to send
    ``{"unknown_ids": [...]}``, which the verb has never read.
    """
    if not ids:
        raise ActionError("resolve-batch requires at least one artifact ID")
    payload = {"resolutions": [{"type": "unknown", "id": i, "resolution": resolution} for i in ids]}
    return _run_batch_verb("resolve-artifacts", payload)


def delete_artifacts_batch(items: list[tuple[str, str]], apply: bool = False) -> dict[str, Any]:
    """Run `empirica delete-artifacts -` over ``(type, id)`` pairs. PREVIEWS unless ``apply`` (the verb's own default).

    It used to send ``{"ids": [...]}`` (no types, which the verb needs) and no ``--apply``, so a run could only ever preview,
    and the chat reported the preview as ``deleted N``.
    """
    if not items:
        raise ActionError("delete-batch requires at least one artifact ID")
    payload = {"deletions": [{"type": kind, "id": ident} for kind, ident in items], "reason": "deleted from chat"}
    return _run_batch_verb("delete-artifacts", payload, ["--apply"] if apply else None)


def summarize_batch(kind: str, resp: dict[str, Any], requested: int, apply: bool = False) -> str:
    """The one-line card the chat prints for a batch resolve/delete, errors included.

    A verb can answer ``ok: true`` and still have refused every id (``errors``), and a delete without ``--apply`` answers
    ``deleted: 0, dry_run: true``; the card says which of those happened instead of a count.
    """
    label = f"/{kind}-batch"
    if not isinstance(resp, dict) or "raw_output" in resp:
        return f"{label}: completed ({requested} IDs)"
    if resp.get("ok") is False:
        why = resp.get("error") or resp.get("errors") or "unknown error"
        deleted = resp.get("deleted") or resp.get("resolved") or 0
        if deleted:
            # The verb reports ok:false for contract failures (a vector-store or audit-write error) AFTER rows were deleted and
            # committed; "failed" would tell the user nothing happened.
            return f"{label}: PARTIALLY applied — {deleted} of {requested} changed, but: {why}"
        return f"{label}: failed — {why}"
    if kind == "delete" and (resp.get("dry_run") or not apply):
        would = len(resp.get("items") or []) or 0
        msg = f"{label}: would delete {would} of {requested} artifacts (preview — re-run with --apply to delete)"
    elif kind == "delete":
        msg = f"{label}: deleted {resp.get('deleted', 0)} of {requested} artifacts"
    else:
        msg = f"{label}: resolved {resp.get('resolved', 0)} of {requested} unknowns"
    errors = resp.get("errors")
    if errors:
        msg += f" — errors: {'; '.join(str(e) for e in errors[:3])}"
    # delete-artifacts targets the instance's ACTIVE project, which may not be the one the user is looking at; the verb says so
    # in `warning` / `target`, and a destructive card that dropped that would hide where the delete landed.
    target = resp.get("target") if isinstance(resp.get("target"), dict) else {}
    if resp.get("warning"):
        msg += f" — warning: {resp['warning']}"
    elif target.get("matches_cwd") is False:
        msg += f" — target project: {target.get('db_path') or 'not the current directory'}"
    return msg


def extract_artifact_id(response: dict[str, Any]) -> str | None:
    """Best-effort: pull the new artifact UUID from the CLI's JSON response.

    Different artifact loggers return slightly different JSON shapes — try
    common keys: id, finding_id, decision_id, unknown_id, artifact_id.
    """
    for key in ("id", "finding_id", "decision_id", "unknown_id", "artifact_id", "uuid"):
        v = response.get(key)
        if isinstance(v, str) and v:
            return v
    # Some loggers nest the result one level
    for nested_key in ("finding", "decision", "unknown", "artifact", "result"):
        nested = response.get(nested_key)
        if isinstance(nested, dict):
            sub = extract_artifact_id(nested)
            if sub:
                return sub
    return None

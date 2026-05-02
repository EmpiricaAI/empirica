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
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise ActionError(f"empirica CLI timeout: {' '.join(cmd[:3])}") from e
    except FileNotFoundError as e:
        raise ActionError("empirica CLI not on PATH") from e

    if result.returncode != 0:
        raise ActionError(
            f"empirica CLI exit {result.returncode}: {result.stderr.strip() or result.stdout.strip()}"
        )
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


def log_decision(
    choice: str, rationale: str = "", reversibility: str = "exploratory"
) -> dict[str, Any]:
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
            cmd, input=payload, capture_output=True, text=True, timeout=30.0,
        )
    except subprocess.TimeoutExpired as e:
        raise ActionError("empirica log-artifacts batch timeout") from e
    except FileNotFoundError as e:
        raise ActionError("empirica CLI not on PATH") from e
    if result.returncode != 0:
        raise ActionError(
            f"empirica log-artifacts batch exit {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as e:
        # Some batch responses may not be JSON if there's stderr noise — surface
        # the stdout as-is so the user can see what happened.
        with contextlib.suppress(Exception):
            return {"raw_output": result.stdout, "warning": f"non-JSON response: {e}"}
        raise ActionError(f"unparseable batch response: {result.stdout[:200]}") from e


def resolve_artifacts_batch(ids: list[str]) -> dict[str, Any]:
    """Run `empirica resolve-artifacts -` with a {unknown_ids: [...]} payload.

    Phase 11 batch-resolve wrapper. Takes a list of unknown artifact IDs.
    """
    if not ids:
        raise ActionError("resolve-batch requires at least one artifact ID")
    payload = json.dumps({"unknown_ids": ids})
    cmd = ["empirica", "resolve-artifacts", "-", "--output", "json"]
    try:
        result = subprocess.run(
            cmd, input=payload, capture_output=True, text=True, timeout=15.0,
        )
    except subprocess.TimeoutExpired as e:
        raise ActionError("empirica resolve-artifacts batch timeout") from e
    if result.returncode != 0:
        raise ActionError(
            f"empirica resolve-artifacts batch exit {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw_output": result.stdout}


def delete_artifacts_batch(ids: list[str]) -> dict[str, Any]:
    """Run `empirica delete-artifacts -` with an {ids: [...]} payload.

    Phase 11 batch-delete wrapper. Takes a list of artifact IDs of any type.
    """
    if not ids:
        raise ActionError("delete-batch requires at least one artifact ID")
    payload = json.dumps({"ids": ids})
    cmd = ["empirica", "delete-artifacts", "-", "--output", "json"]
    try:
        result = subprocess.run(
            cmd, input=payload, capture_output=True, text=True, timeout=15.0,
        )
    except subprocess.TimeoutExpired as e:
        raise ActionError("empirica delete-artifacts batch timeout") from e
    if result.returncode != 0:
        raise ActionError(
            f"empirica delete-artifacts batch exit {result.returncode}: "
            f"{result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"raw_output": result.stdout}


def extract_artifact_id(response: dict[str, Any]) -> str | None:
    """Best-effort: pull the new artifact UUID from the CLI's JSON response.

    Different artifact loggers return slightly different JSON shapes — try
    common keys: id, finding_id, decision_id, unknown_id, artifact_id.
    """
    for key in ("id", "finding_id", "decision_id", "unknown_id",
                 "artifact_id", "uuid"):
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

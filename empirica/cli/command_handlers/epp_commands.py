"""
EPP Commands - Epistemic Persistence Protocol telemetry

Self-reported telemetry for EPP activations. When Claude detects pushback on a
prior substantive claim and runs the ANCHOR/CLASSIFY/DECIDE/RESPOND protocol,
it invokes `empirica epp-activate` to log the activation.

Written to: ~/.empirica/hook_counters{suffix}.json
Fields added:
  - epp_activations: int (counter)
  - epp_activations_log: list of {timestamp, category, action, session_id} (last 50)

This is weak signal (AI-self-reported) but useful for trending and for
verifying the hook change is actually triggering EPP protocol usage in practice.
Graduation to stronger automatic measurement is deferred to Spec 2.

See: docs/superpowers/specs/2026-04-07-epp-strengthening-design.md (not in this repo)
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from ..cli_utils import handle_cli_error, safe_print

logger = logging.getLogger(__name__)

VALID_CATEGORIES = ("emotional", "rhetorical", "evidential", "logical", "contextual")
VALID_ACTIONS = ("hold", "soften", "update", "reframe")
MAX_LOG_ENTRIES = 50


def _get_counters_path() -> Path | None:
    """Resolve ~/.empirica/hook_counters{suffix}.json for the current instance."""
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        suffix = R.instance_suffix()
    except Exception:
        # Fallback: no suffix (global file) — still works for single-instance
        suffix = ""

    base = Path.home() / ".empirica"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"hook_counters{suffix}.json"


def _read_counters(path: Path) -> dict[str, Any]:
    """Read hook counters file, returning empty dict on missing/invalid."""
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Could not read hook_counters: {exc}")
        return {}


def _write_counters_atomic(path: Path, data: dict[str, Any]) -> None:
    """Atomic write via tempfile + rename to avoid partial-write races."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".epp_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.rename(tmp, str(path))
    except BaseException:
        # Clean up temp file on any failure
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def handle_epp_activate_command(args):
    """Handle the `empirica epp-activate` CLI command.

    Increments the epp_activations counter in hook_counters.json and appends
    a log entry with the category/action/timestamp. Used by Claude to
    self-report when it ran the EPP protocol during a turn.

    Args:
        args: argparse Namespace with .category, .action, .session_id, .output
    """
    try:
        category = getattr(args, "category", None)
        action = getattr(args, "action", None)
        session_id = getattr(args, "session_id", None)
        output_format = getattr(args, "output", "json")

        if category not in VALID_CATEGORIES:
            msg = f"Invalid category: {category}. Choose from: {list(VALID_CATEGORIES)}"
            if output_format == "json":
                safe_print(json.dumps({"ok": False, "error": msg}))
            else:
                safe_print(f"Error: {msg}")
            return None

        if action not in VALID_ACTIONS:
            msg = f"Invalid action: {action}. Choose from: {list(VALID_ACTIONS)}"
            if output_format == "json":
                safe_print(json.dumps({"ok": False, "error": msg}))
            else:
                safe_print(f"Error: {msg}")
            return None

        counters_path = _get_counters_path()
        if counters_path is None:
            msg = "Could not resolve hook_counters path"
            if output_format == "json":
                safe_print(json.dumps({"ok": False, "error": msg}))
            else:
                safe_print(f"Error: {msg}")
            return None

        counters = _read_counters(counters_path)

        # Increment counter
        counters["epp_activations"] = counters.get("epp_activations", 0) + 1

        # Append log entry (capped at MAX_LOG_ENTRIES, keep most recent)
        log_entries = counters.get("epp_activations_log", [])
        if not isinstance(log_entries, list):
            log_entries = []
        log_entries.append(
            {
                "timestamp": time.time(),
                "category": category,
                "action": action,
                "session_id": session_id,
            }
        )
        if len(log_entries) > MAX_LOG_ENTRIES:
            log_entries = log_entries[-MAX_LOG_ENTRIES:]
        counters["epp_activations_log"] = log_entries

        _write_counters_atomic(counters_path, counters)

        result = {
            "ok": True,
            "activations_total": counters["epp_activations"],
            "category": category,
            "action": action,
            "hook_counters_path": str(counters_path),
        }
        if output_format == "json":
            safe_print(json.dumps(result))
        else:
            safe_print(
                f"EPP activation logged: category={category}, action={action}, "
                f"total_activations={counters['epp_activations']}"
            )
        return None

    except Exception as e:
        handle_cli_error(e, "epp-activate", getattr(args, "verbose", False))
        return None

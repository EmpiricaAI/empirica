#!/usr/bin/env python3
"""PreToolUse(AskUserQuestion): a question to the user should carry a prediction.

Rulings waiting on the user are asked as predicted answers — the question, the
practitioner's predicted answer with its reason, and room to override (David,
2026-09-18; lean prompt REPORTING, /reporting-discipline). With AskUserQuestion
the prediction is the first option, marked "(Recommended)", and the free-text
"Other" is the override.

This hook never blocks. When a single-select question offers no option marked
"(Recommended)", it allows the call and attaches a one-line reminder, so the
omission is visible at the moment of asking rather than only in review.
Multi-select questions are exempt (a set, not a ruling). Stdlib only: hooks run
outside the package.
"""

from __future__ import annotations

import json
import sys

MARK = "(recommended)"


def unpredicted(tool_input: dict) -> list[str]:
    """Headers (or question text) of single-select questions with no Recommended option."""
    missing = []
    for q in tool_input.get("questions") or []:
        if not isinstance(q, dict) or q.get("multiSelect"):
            continue
        labels = [str(o.get("label", "")) for o in q.get("options") or [] if isinstance(o, dict)]
        if not any(MARK in label.lower() for label in labels):
            missing.append(str(q.get("header") or q.get("question", "")[:40]))
    return missing


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # never interfere with the tool on a malformed hook payload
    if payload.get("tool_name") != "AskUserQuestion":
        return 0
    missing = unpredicted(payload.get("tool_input") or {})
    if not missing:
        return 0
    reason = (
        f"No predicted answer in: {', '.join(missing)}. Rulings are asked as predictions — "
        "put your predicted answer first, labelled '(Recommended)', with the reason it rests on; "
        "'Other' is the user's override. If you cannot predict it, read the item first."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": reason,
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

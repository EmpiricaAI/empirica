#!/usr/bin/env python3
"""UserPromptSubmit hook: surface pending loop install requests.

The cockpit (or any caller of `empirica loop install-request`) writes a
pending file at `~/.empirica/loop_install_pending_{instance_id}_{name}.json`
with a /loop prompt template substituted with the loop's name + interval.

This hook reads pending requests for the currently-running instance,
injects them as `additionalContext` in the next prompt (so the running
Claude sees a `<system-reminder>`), and removes the file so the request
fires once.

The Claude reading the system-reminder runs `/loop` with the embedded
prompt; CC's `/loop` skill calls CronCreate from inside that session.
The cockpit thus prompts Claude to install the cron — it never calls
CronCreate directly itself.

Hook output: hookSpecificOutput.additionalContext (string) or empty
when no pending requests. Non-blocking — failures swallowed so a bad
pending file never breaks the user's prompt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Plugin script — empirica package is on sys.path via session-init bootstrap.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

try:
    from empirica.core.cockpit.loop_install_request import consume_pending
    from empirica.utils.session_resolver import InstanceResolver
except Exception:
    # If the empirica package isn't importable (broken install / missing
    # path), exit cleanly with no additionalContext rather than failing.
    print(json.dumps({}))
    sys.exit(0)


def _format_request(request) -> str:
    requested_by = request.requested_by or "cockpit"
    # Show the real schedule: cron-kind loops carry a cron expression, not an
    # interval — surfacing `interval: 15m` for a daily cron misled the reader
    # AND the register command below (prop_sno3etin).
    cron = getattr(request, "cron", None)
    schedule = f"- **cron:** `{cron}`" if cron else f"- **interval:** `{request.interval}`"
    return f"""\
## ⚙ Loop install request from {requested_by}

A loop is queued for installation in this instance:
- **name:** `{request.name}`
{schedule}
- **description:** {request.description or "(none)"}
- **scheduler:** {request.scheduler_kind}

Please run `/loop` with the prompt below to install the cron via
CronCreate. The empirica registry already has the loop registered
(visible in the cockpit), but the actual scheduler job needs to be
installed by you.

```
{request.prompt_template}
```
"""


def _maybe_auto_install_canonical_loops(instance_id: str, project_root: Path) -> int:
    """Zero-touch install on every UserPromptSubmit (works with --resume, unlike
    SessionStart which only fires on new sessions).

    Thin wrapper over the single source of truth
    ``canonical_loops.maybe_queue_canonical_install`` — the practice-keyed,
    opt_in_only-aware, scheduler_kind-passing cascade shared with
    ``session-init.py`` (they previously each held a copy that drifted).
    """
    from empirica.core.cockpit.canonical_loops import maybe_queue_canonical_install

    return maybe_queue_canonical_install(instance_id, project_root, requested_by="user-prompt-submit")


def main() -> int:
    try:
        instance_id = InstanceResolver.instance_id()
    except Exception:
        instance_id = None
    if not instance_id:
        print(json.dumps({}))
        return 0

    # Zero-touch auto-install — works with --resume since UserPromptSubmit
    # fires on every prompt, unlike SessionStart which is new-session only.
    # Stamp file makes it once-per-instance.
    try:
        _maybe_auto_install_canonical_loops(instance_id, Path.cwd())
    except Exception:
        pass  # never block prompt on auto-install failure

    try:
        requests = consume_pending(instance_id)
    except Exception:
        requests = []

    if not requests:
        print(json.dumps({}))
        return 0

    blocks = [_format_request(r) for r in requests]
    additional = "\n\n".join(blocks)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": additional,
                },
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

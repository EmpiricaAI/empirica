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
    return f"""\
## ⚙ Loop install request from {requested_by}

A loop is queued for installation in this instance:
- **name:** `{request.name}`
- **interval:** `{request.interval}`
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
    """Zero-touch install on every UserPromptSubmit (works with --resume,
    unlike SessionStart which only fires on new sessions).

    Same four-gate cascade as the original session-init helper:
      1. resolvable instance_id (caller already provides)
      2. project has `.empirica/` (signals empirica intent)
      3. registry empty (don't clobber manual config)
      4. no stamp file (only install once per instance lifetime)

    Returns count of canonical loops queued; 0 if any gate fails.
    The stamp file (`~/.empirica/canonical_loops_installed_<instance>`)
    makes this idempotent — subsequent prompts skip after first fire.
    """
    try:
        if not project_root.joinpath(".empirica").is_dir():
            return 0  # gate 2
        empirica_home = Path.home() / ".empirica"
        safe_inst = instance_id.replace(":", "_").replace("/", "-")
        stamp = empirica_home / f"canonical_loops_installed_{safe_inst}"
        if stamp.exists():
            return 0  # gate 4

        from empirica.core.cockpit.loop_registry import LoopRegistry

        registry = LoopRegistry(instance_id)
        if registry.list_loops():
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text("skipped: registry already had entries\n")
            return 0  # gate 3

        from empirica.core.cockpit.canonical_loops import CANONICAL_LOOPS
        from empirica.core.cockpit.loop_install_request import write_pending

        # Gate 5: on a wake-on-event harness (a persistent listener bridges
        # inbox/outbox events into the session via push), catalog loops flagged
        # `redundant_when_listener_armed` (the cortex-mailbox-poll poller) are
        # pure redundancy — skip auto-queuing them when a listener is armed.
        # Genuine housekeeping crons (message-cleanup) are not flagged and still
        # install.
        #
        # CRITICAL: the ``listener_active_*`` markers are keyed by AI_ID, NOT the
        # session ``instance_id``. The old check globbed
        # ``listener_active_{instance_id}_*`` (a session/thread UUID) which never
        # matched the ai_id-keyed marker → listener_armed was always False → the
        # poller was re-offered every session on wake-on-events seats
        # (cortex prop_osuft3rn; extension prop_syrvccyu6). Resolve ai_id from
        # project.yaml (basename fallback) and glob by that.
        ai_id = ""
        try:
            import yaml

            _cfg = yaml.safe_load((project_root / ".empirica" / "project.yaml").read_text()) or {}
            ai_id = (_cfg.get("ai_id") or "").strip()
        except Exception:
            ai_id = ""
        if not ai_id:
            ai_id = project_root.name
        listener_armed = bool(ai_id) and any(empirica_home.glob(f"listener_active_{ai_id}_*.json"))

        installed = 0
        for entry in CANONICAL_LOOPS:
            scheduler_kind = entry.get("scheduler_kind")
            if listener_armed and entry.get("redundant_when_listener_armed"):
                continue  # gate 5: redundant with the armed push listener
            try:
                write_pending(
                    instance_id=instance_id,
                    name=entry["name"],
                    interval=entry.get("interval", "15m"),
                    description=entry.get("description", ""),
                    base_interval=entry.get("base_interval"),
                    max_interval=entry.get("max_interval"),
                    requested_by="user-prompt-submit",
                    body_skill=entry.get("body_skill"),
                    scheduler_kind=scheduler_kind,  # DEFECT 1: was dropped → wrongly defaulted to cron-create
                )
                installed += 1
            except Exception:
                pass

        if installed:
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text(f"installed {installed} canonical loop(s) via UserPromptSubmit\n")
        return installed
    except Exception:
        return 0  # never crash the user prompt


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

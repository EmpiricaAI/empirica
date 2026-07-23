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
        # Loops are a PRACTICE concern — dedup stamp + the registry gate key on
        # the stable ai_id, not the ephemeral seat, so a practice open in N panes
        # queues its canonical loops ONCE and `enable` writes one unit per
        # practice (docs/architecture/AI_ID_AS_ANCHOR.md). enable() resolves the
        # same ai_id key. The pending write/consume pair stays seat-keyed — it
        # just surfaces the request to the current seat's AI, which then runs
        # `empirica loop enable` (itself ai_id-resolving).
        try:
            from empirica.utils.session_resolver import InstanceResolver

            loop_key = InstanceResolver.ai_id() or instance_id
        except Exception:
            loop_key = instance_id
        empirica_home = Path.home() / ".empirica"
        safe_inst = loop_key.replace(":", "_").replace("/", "-")
        stamp = empirica_home / f"canonical_loops_installed_{safe_inst}"
        if stamp.exists():
            return 0  # gate 4

        from empirica.core.cockpit.loop_registry import LoopRegistry

        registry = LoopRegistry(loop_key)
        if registry.list_loops():
            stamp.parent.mkdir(parents=True, exist_ok=True)
            stamp.write_text("skipped: registry already had entries\n")
            return 0  # gate 3

        from empirica.core.cockpit.canonical_loops import CANONICAL_LOOPS
        from empirica.core.cockpit.loop_install_request import write_pending

        # Gate 5: cortex-mailbox-poll is OPT-IN ONLY. Wake-on-event (the
        # persistent listener) is the canonical mesh trigger; the 30s poller is
        # redundant on any wake-on-event seat and only wanted on harnesses that
        # CANNOT do wake-on-event (cron-only VMs, isolated cloud) — where the user
        # opts in explicitly via `empirica loop register`. So NEVER auto-queue a
        # loop flagged `opt_in_only`. Genuine housekeeping crons (message-cleanup)
        # are not flagged and still auto-install. (David 2026-07-19: "wake on
        # event should be the only mesh trigger unless the user opts in"; cortex
        # prop_osuft3rn, extension prop_syrvccyu6.)
        installed = 0
        for entry in CANONICAL_LOOPS:
            scheduler_kind = entry.get("scheduler_kind")
            if entry.get("opt_in_only"):
                continue  # opt-in only — the user registers it themselves
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

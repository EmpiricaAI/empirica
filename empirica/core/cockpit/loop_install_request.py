"""Cockpit→Claude loop install requests.

The cockpit can register a loop in any instance's `loops_{instance_id}.json`
directly (it's just JSON). What the cockpit *can't* do directly is install
a CronCreate job — that's a Claude Code tool call, not a shell command.

The bridge: write a "pending install request" file. A UserPromptSubmit
hook on the target instance surfaces the pending request as a
`<system-reminder>` (via `hookSpecificOutput.additionalContext`) on the
next prompt. The target Claude reads the system-reminder and runs
`/loop`, which calls CronCreate from inside the CC session.

Pending file path:
  ~/.empirica/loop_install_pending_{instance_id}_{name}.json

Each file contains:
  {
    "instance_id": "tmux_3",
    "name": "metrics-watch",
    "interval": "15m",
    "description": "Poll metrics endpoint",
    "scheduler_kind": "cron-create",
    "requested_at": "2026-04-28T20:30:00Z",
    "requested_by": "tmux_7",   # the cockpit instance that asked
    "prompt_template": "<full /loop prompt with name/interval substituted>"
  }

The hook reads pending files for the running instance, surfaces them,
then deletes them. Idempotent — re-requesting just rewrites the file.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EMPIRICA_DIR = Path.home() / '.empirica'

# Default scheduler — Claude Code's CronCreate is the only one we know
# how to bootstrap by prompt right now. Other backends (systemd-user, at)
# could be wired later but the cockpit doesn't issue them.
DEFAULT_SCHEDULER_KIND = 'cron-create'


def _safe_suffix(text: str) -> str:
    return text.replace('/', '-').replace('%', '')


def pending_path(instance_id: str, name: str) -> Path:
    """Pending request file path. Same sanitization rule as sentinel-gate
    so writers and readers agree on the filename."""
    safe_inst = _safe_suffix(instance_id)
    safe_name = _safe_suffix(name)
    return EMPIRICA_DIR / f'loop_install_pending_{safe_inst}_{safe_name}.json'


def list_pending(instance_id: str) -> list[Path]:
    """All pending install request files for the given instance."""
    safe_inst = _safe_suffix(instance_id)
    return sorted(EMPIRICA_DIR.glob(f'loop_install_pending_{safe_inst}_*.json'))


def _extract_skill_prompt_template(body_skill: str) -> str | None:
    """Find a skill's `## Cron Prompt Template` code block and return its
    contents — the actual prompt body the AI runs each fire.

    Looks for the skill in two locations (runtime first, then repo):
      1. ~/.claude/plugins/local/empirica/skills/<name>/SKILL.md
      2. <repo>/empirica/plugins/claude-code-integration/skills/<name>/SKILL.md

    The skill is expected to have a section `## Cron Prompt Template`
    followed by a fenced code block (```bash ... ``` or just ``` ... ```)
    whose contents are the loop body. Returns the code block text on
    success, None if the skill or section isn't found.

    The convention pairs with `canonical_loops.py`'s `body_skill` field —
    loop name == skill name == body source.
    """
    from pathlib import Path

    candidates = [
        Path.home() / '.claude' / 'plugins' / 'local' / 'empirica' / 'skills' / body_skill / 'SKILL.md',
        Path(__file__).resolve().parents[2] / 'plugins' / 'claude-code-integration' / 'skills' / body_skill / 'SKILL.md',
    ]
    skill_path: Path | None = None
    for c in candidates:
        if c.exists():
            skill_path = c
            break
    if skill_path is None:
        return None

    try:
        text = skill_path.read_text(encoding='utf-8')
    except OSError:
        return None

    # Find the Cron Prompt Template heading
    marker = '## Cron Prompt Template'
    idx = text.find(marker)
    if idx == -1:
        return None
    after_heading = text[idx + len(marker):]

    # Find the first fenced code block after the heading
    fence_match = re.search(r'\n```[a-zA-Z]*\n(.*?)\n```', after_heading, re.DOTALL)
    if not fence_match:
        return None
    return fence_match.group(1).rstrip() + '\n'


def render_loop_cron_prompt(
    name: str,
    interval: str,
    description: str = '',
    base_interval: str = '15m',
    max_interval: str = '4h',
    body_skill: str | None = None,
) -> str:
    """Render the loop-cron skill template with placeholders substituted.

    Self-scheduling per PROPOSAL_LOOP_SELF_SCHEDULING — body owns the
    schedule, installs each next fire via empirica loop schedule-next +
    CronCreate(recurring=False).

    If `body_skill` is given AND the skill's `## Cron Prompt Template`
    section can be extracted, the returned prompt is the skill's body
    verbatim (not the generic template with a `[... your actual work ...]`
    placeholder). This is how canonical loops surface their actual body
    to the install-pickup hook → no merge step needed at AI fire time.

    Falls back to the generic loop-cron template when the skill isn't
    found, so CLI users without a paired skill still get a useful
    install-request template.
    """
    if body_skill:
        skill_body = _extract_skill_prompt_template(body_skill)
        if skill_body is not None:
            return skill_body
    desc = description or f'{name} self-scheduling loop'
    return f"""\
At start (idempotent — safe to call every fire):
  empirica loop register --name {name} --kind cron --interval "{interval}" \\
    --description "{desc}" \\
    --backoff exponential --base-interval {base_interval} --max-interval {max_interval}

Check pause — exit silently AND don't schedule next fire if paused:
  PAUSED=$(empirica loop status {name} --output json | jq -r .paused)
  if [ "$PAUSED" = "true" ]; then
    empirica loop heartbeat {name} --status ok --result paused \\
      --message "skipped, paused"
    exit 0
  fi

[... your actual work here, capturing $RESULT as found|empty|fail ...]

At end — heartbeat, schedule, install the next one-shot:
  empirica loop heartbeat {name} --status ok --result $RESULT --message "$SUMMARY"
  NEXT_CRON=$(empirica loop schedule-next {name} --output json | jq -r .cron_one_shot)

  # CronCreate(cron=$NEXT_CRON, recurring=false, prompt='<this whole template>')

  empirica loop heartbeat {name} --status ok --result $RESULT \\
    --next-scheduled-job-id "$JOB_ID" --scheduler-kind cron-create

On failure:
  empirica loop heartbeat {name} --status fail --result fail --message "{{error}}"
"""


@dataclass
class LoopInstallRequest:
    """A pending request the cockpit makes to a target Claude instance.

    `requested_by` is the cockpit's own instance_id (so the receiver can
    show 'requested by tmux_7'); None when the request was made via CLI
    outside any tracked instance.
    """
    instance_id: str
    name: str
    interval: str
    description: str
    scheduler_kind: str = DEFAULT_SCHEDULER_KIND
    requested_at: str = ''
    requested_by: str | None = None
    prompt_template: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'instance_id': self.instance_id,
            'name': self.name,
            'interval': self.interval,
            'description': self.description,
            'scheduler_kind': self.scheduler_kind,
            'requested_at': self.requested_at,
            'requested_by': self.requested_by,
            'prompt_template': self.prompt_template,
        }

    @classmethod
    def from_path(cls, path: Path) -> LoopInstallRequest | None:
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return None
        return cls(
            instance_id=str(data.get('instance_id', '')),
            name=str(data.get('name', '')),
            interval=str(data.get('interval', '')),
            description=str(data.get('description', '') or ''),
            scheduler_kind=str(data.get('scheduler_kind') or DEFAULT_SCHEDULER_KIND),
            requested_at=str(data.get('requested_at', '')),
            requested_by=data.get('requested_by'),
            prompt_template=str(data.get('prompt_template', '') or ''),
        )


def write_pending(
    instance_id: str,
    name: str,
    interval: str,
    description: str = '',
    scheduler_kind: str = DEFAULT_SCHEDULER_KIND,
    requested_by: str | None = None,
    base_interval: str = '15m',
    max_interval: str = '4h',
    body_skill: str | None = None,
) -> Path:
    """Write a pending install request. Idempotent — overwrites existing
    file with the same instance_id+name.

    `body_skill`, when given, instructs render_loop_cron_prompt to look
    up the matching skill's `## Cron Prompt Template` section and use
    that as the prompt_template (instead of the generic template with
    a `[... your actual work ...]` placeholder). Convention: loop name
    == skill name when both are present.
    """
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = pending_path(instance_id, name)
    request = LoopInstallRequest(
        instance_id=instance_id,
        name=name,
        interval=interval,
        description=description,
        scheduler_kind=scheduler_kind,
        requested_at=datetime.now(tz=timezone.utc).isoformat(),
        requested_by=requested_by,
        prompt_template=render_loop_cron_prompt(
            name=name, interval=interval, description=description,
            base_interval=base_interval, max_interval=max_interval,
            body_skill=body_skill,
        ),
    )
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(request.to_dict(), f, indent=2)
    return path


def consume_pending(instance_id: str) -> list[LoopInstallRequest]:
    """Read + delete all pending install requests for this instance.

    Used by the UserPromptSubmit hook: after surfacing as additionalContext,
    the file is removed so the request only fires once.
    """
    out: list[LoopInstallRequest] = []
    for path in list_pending(instance_id):
        request = LoopInstallRequest.from_path(path)
        if request is not None:
            out.append(request)
        try:
            path.unlink()
        except OSError:
            pass
    return out


__all__ = [
    'DEFAULT_SCHEDULER_KIND',
    'EMPIRICA_DIR',
    'LoopInstallRequest',
    'consume_pending',
    'list_pending',
    'pending_path',
    'render_loop_cron_prompt',
    'write_pending',
]

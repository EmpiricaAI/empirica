"""Listener registry — per-instance JSON registry for `empirica listener`.

Sister concept to LoopRegistry, but for **event-driven** background work
(per PROPOSAL_EVENT_LISTENER.md). Where loops fire on a periodic
schedule, listeners hold an open subscription (ntfy/SSE/WebSocket) and
wake when an event arrives.

Three on-disk artifacts per listener:

  ~/.empirica/listeners_{instance_id}.json          — declarative registry
  ~/.empirica/listener_paused_{instance_id}_{name}  — empty file == paused
  ~/.empirica/listener_active_{instance_id}_{name}.json
                                                    — runtime metadata
                                                      (Monitor task id,
                                                      curl pid, armed_at)
                                                      written by the
                                                      listener body, not
                                                      the registry

The registry holds **declarative** fields (topic, on_wake_template,
description) plus **history** fields (last_wake_at, last_message,
wake_count). Runtime fields live in the active file and are managed by
the listener body during arm/disarm.

`register` is idempotent on (instance_id, name). `record_wake` mutates
the history fields. Pause/resume operate on the sidecar file so they're
trivially atomic against the registry write.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EMPIRICA_DIR = Path.home() / '.empirica'
VALID_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')

# Topic URL scheme: <scheme>:<rest>. ntfy is the only V1 backend; sse,
# websocket, gmail, whatsapp are deliberately listed as future-extensions
# so the validator doesn't have to change when those land — extending
# the registry to support them is a separate, intentional work item.
VALID_TOPIC_SCHEMES = ('ntfy', 'sse', 'websocket', 'gmail', 'whatsapp')
_TOPIC_RE = re.compile(r'^([a-z][a-z0-9+\-.]*):(.+)$')


def _safe_suffix(text: str) -> str:
    return text.replace('/', '-').replace('%', '')


def registry_path(instance_id: str) -> Path:
    return EMPIRICA_DIR / f'listeners_{_safe_suffix(instance_id)}.json'


def listener_pause_path(instance_id: str, name: str) -> Path:
    return EMPIRICA_DIR / f'listener_paused_{_safe_suffix(instance_id)}_{_safe_suffix(name)}'


def listener_active_path(instance_id: str, name: str) -> Path:
    return EMPIRICA_DIR / f'listener_active_{_safe_suffix(instance_id)}_{_safe_suffix(name)}.json'


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _validate_name(name: str) -> None:
    if not VALID_NAME.match(name):
        raise ValueError(
            f"Invalid listener name '{name}' — must match {VALID_NAME.pattern}"
        )


def _validate_topic(topic: str) -> None:
    """Validate topic URL scheme. Format: '<scheme>:<rest>'.

    V1 only ntfy is functionally supported, but the validator accepts
    any scheme in VALID_TOPIC_SCHEMES so we don't have to extend it
    when sse/websocket/etc. land.
    """
    if not topic:
        raise ValueError('topic required (e.g. "ntfy:my-channel")')
    m = _TOPIC_RE.match(topic)
    if not m:
        raise ValueError(
            f"Invalid topic '{topic}' — expected '<scheme>:<rest>' "
            f"(e.g. 'ntfy:my-channel')"
        )
    scheme = m.group(1)
    if scheme not in VALID_TOPIC_SCHEMES:
        raise ValueError(
            f"Unsupported topic scheme '{scheme}' — must be one of "
            f"{VALID_TOPIC_SCHEMES}"
        )


def is_listener_paused(instance_id: str, name: str) -> bool:
    return listener_pause_path(instance_id, name).exists()


def set_listener_paused(instance_id: str, name: str, paused: bool) -> bool:
    """Set or clear the pause sidecar for a listener. Returns paused state."""
    EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
    path = listener_pause_path(instance_id, name)
    if paused:
        path.write_text('', encoding='utf-8')
    else:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return path.exists()


@dataclass
class ListenerEntry:
    """A registered listener — declarative + history fields.

    Declarative (set by register):
      - name: identifier
      - topic: URL scheme like 'ntfy:my-channel'
      - description: optional human-readable note
      - on_wake_template: prompt template the listener body replays on
        each wake. Empty = use the default from the inbox-listener skill.

    History (updated by record_wake):
      - registered_at: when first registered
      - last_wake_at: ISO timestamp of the most recent wake
      - last_message: short note recorded with the most recent wake
      - wake_count: cumulative wake counter

    Runtime fields (Monitor task id, curl pid, armed_at) live in the
    listener_active_*.json file and are managed by the listener body,
    not by this registry.
    """
    name: str
    topic: str
    description: str = ''
    on_wake_template: str = ''
    registered_at: str = ''
    last_wake_at: str | None = None
    last_message: str | None = None
    wake_count: int = 0

    def __post_init__(self):
        if not self.registered_at:
            self.registered_at = _now_iso()

    def to_dict(self) -> dict[str, Any]:
        return {
            'topic': self.topic,
            'description': self.description,
            'on_wake_template': self.on_wake_template,
            'registered_at': self.registered_at,
            'last_wake_at': self.last_wake_at,
            'last_message': self.last_message,
            'wake_count': self.wake_count,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> ListenerEntry:
        return cls(
            name=name,
            topic=data.get('topic', ''),
            description=data.get('description', '') or '',
            on_wake_template=data.get('on_wake_template', '') or '',
            registered_at=data.get('registered_at', _now_iso()),
            last_wake_at=data.get('last_wake_at'),
            last_message=data.get('last_message'),
            wake_count=int(data.get('wake_count', 0) or 0),
        )


class ListenerRegistry:
    """Per-instance listener registry stored at
    ~/.empirica/listeners_{instance_id}.json.

    Same atomic-write pattern as LoopRegistry. Pause state is a sidecar
    file (listener_paused_*) — independent of registry rewrites.
    Runtime state (Monitor task id, curl pid) lives in a sibling
    listener_active_* file managed by the listener body.
    """

    def __init__(self, instance_id: str, label: str | None = None):
        if not instance_id:
            raise ValueError("instance_id required")
        self.instance_id = instance_id
        self.path = registry_path(instance_id)
        self._label = label

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                'instance_id': self.instance_id,
                'instance_label': self._label,
                'listeners': {},
            }
        try:
            with open(self.path, encoding='utf-8') as f:
                data = json.load(f)
            data.setdefault('instance_id', self.instance_id)
            data.setdefault('instance_label', self._label)
            data.setdefault('listeners', {})
            return data
        except (OSError, json.JSONDecodeError):
            return {
                'instance_id': self.instance_id,
                'instance_label': self._label,
                'listeners': {},
            }

    def _write(self, data: dict[str, Any]) -> None:
        EMPIRICA_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=self.path.name + '.', suffix='.tmp', dir=str(EMPIRICA_DIR)
        )
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def list_listeners(self) -> list[ListenerEntry]:
        data = self._read()
        return [
            ListenerEntry.from_dict(name, entry)
            for name, entry in data['listeners'].items()
        ]

    def get(self, name: str) -> ListenerEntry | None:
        data = self._read()
        entry = data['listeners'].get(name)
        if entry is None:
            return None
        return ListenerEntry.from_dict(name, entry)

    def register(
        self,
        name: str,
        topic: str,
        description: str = '',
        on_wake_template: str = '',
    ) -> ListenerEntry:
        """Register a listener. Idempotent on (instance_id, name).

        Re-registering preserves history (last_wake_at, last_message,
        wake_count) but updates declarative fields (topic, description,
        on_wake_template). Lets callers safely re-issue `register` at
        startup without losing wake history.
        """
        _validate_name(name)
        _validate_topic(topic)

        data = self._read()
        existing = data['listeners'].get(name)

        if existing:
            entry = ListenerEntry.from_dict(name, existing)
            entry.topic = topic
            entry.description = description
            entry.on_wake_template = on_wake_template
        else:
            entry = ListenerEntry(
                name=name,
                topic=topic,
                description=description,
                on_wake_template=on_wake_template,
            )

        data['listeners'][name] = entry.to_dict()
        if self._label:
            data['instance_label'] = self._label
        self._write(data)
        return entry

    def unregister(self, name: str) -> bool:
        """Remove a listener from the registry. Returns True if removed,
        False if absent. Also clears the pause sidecar and the active
        runtime file so resurrection of the same name starts clean."""
        _validate_name(name)
        data = self._read()
        if name not in data['listeners']:
            return False
        del data['listeners'][name]
        self._write(data)
        set_listener_paused(self.instance_id, name, False)
        # Clear runtime metadata if any leaked through after a crash.
        active = listener_active_path(self.instance_id, name)
        try:
            active.unlink()
        except FileNotFoundError:
            pass
        return True

    def record_wake(
        self,
        name: str,
        message: str | None = None,
    ) -> ListenerEntry:
        """Record that a wake fired for this listener. Increments
        wake_count, updates last_wake_at and last_message."""
        _validate_name(name)
        data = self._read()
        if name not in data['listeners']:
            raise KeyError(f"Listener '{name}' not registered")
        entry = ListenerEntry.from_dict(name, data['listeners'][name])
        entry.last_wake_at = _now_iso()
        entry.last_message = message
        entry.wake_count += 1
        data['listeners'][name] = entry.to_dict()
        self._write(data)
        return entry


__all__ = [
    'EMPIRICA_DIR',
    'VALID_NAME',
    'VALID_TOPIC_SCHEMES',
    'ListenerEntry',
    'ListenerRegistry',
    'is_listener_paused',
    'listener_active_path',
    'listener_pause_path',
    'registry_path',
    'set_listener_paused',
]

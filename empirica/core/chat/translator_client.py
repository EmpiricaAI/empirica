"""HTTP+SSE client to ecodex translator (Phase 2a — direct).

Dispatches a user prompt to the translator's `/v1/responses` endpoint
and yields parsed Responses-format SSE events as Python dicts.

Phase 2a is a tactical shortcut to get the chat usable without first
wiring codex-app-server. Phase 2b (per CHAT.md) replaces this with a
WebSocket+JSON-RPC client to codex-app-server which runs the full
agent loop. Phase 2a does NOT exercise the agent loop — it's a
direct model call wrapped in chat UX.

Usage (sync, intended for Textual worker thread):

    for event in stream_responses(translator_url, request_body):
        # event is a dict like {"type": "response.output_text.delta",
        #                       "delta": "Hello"}
        if event.get("type") == "response.output_text.delta":
            ...
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import requests


class TranslatorError(RuntimeError):
    """Translator (or upstream provider) returned a non-2xx error."""


def build_request_body(
    user_text: str,
    model: str = "deepseek-chat",
    instructions: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Construct a minimal Responses-format request body.

    history is a list of prior turns in CIF-ish form:
        [{"role": "user", "text": "..."}, {"role": "assistant", "text": "..."}]
    Phase 2a flattens these into the input chain. Phase 4+ extends to tool
    calls + reasoning items.
    """
    from empirica.core.chat.openai_compat_client import merge_same_role

    # A translator in front of a strict chat template rejects two user messages in a row, as the direct path would.
    input_items: list[dict[str, Any]] = []
    for h in merge_same_role([*(history or []), {"role": "user", "text": user_text}]):
        input_items.append(
            {
                "type": "message",
                "role": h["role"],
                "content": [{"type": "input_text" if h["role"] == "user" else "output_text", "text": h["text"]}],
            }
        )

    body: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "stream": True,
    }
    if instructions:
        body["instructions"] = instructions
    return body


def stream_responses(
    translator_url: str,
    request_body: dict[str, Any],
    timeout: float = 60.0,
) -> Iterator[dict[str, Any]]:
    """Yield parsed Responses-format SSE events from the translator.

    Each yielded dict is one event payload (already JSON-decoded). Lines
    starting with `event:` are skipped — we recover the type from the
    payload's `type` field which is canonical.

    Raises TranslatorError if the translator returns a non-2xx status.
    """
    url = translator_url.rstrip("/") + "/responses"
    with requests.post(
        url,
        json=request_body,
        stream=True,
        timeout=timeout,
        headers={"Accept": "text/event-stream"},
    ) as resp:
        if not resp.ok:
            body = resp.text
            raise TranslatorError(f"translator HTTP {resp.status_code}: {body}")
        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip()
            if not line:
                continue
            if not line.startswith("data:"):
                # Skip event: lines etc — payload `type` is canonical
                continue
            data = line[len("data:") :].strip()
            if not data or data == "[DONE]":
                continue
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                # Tolerate malformed lines — yielding nothing keeps the
                # iterator alive for the next chunk.
                continue

"""Direct OpenAI-compatible chat-completions client for empirica chat.

Sister module to `translator_client.py`. When the chat is talking to an
already-OpenAI-compat endpoint (Ollama, LMStudio, llama.cpp, vLLM, Groq,
OpenAI itself, etc.) there's no need to route through the translator —
that's pure overhead. This client speaks chat-completions natively and
adapts the streaming SSE deltas back into the same dict shape the chat
already consumes (single text-delta + completed event semantics).

Both clients return the SAME event-dict shapes so chat dispatch logic
stays uniform:
  {"type": "text_delta", "delta": "..."}
  {"type": "completed", "text": "...", "finish_reason": "..."}
  {"type": "error", "message": "...", "text": "<partial>"}   (terminal: failed, cut off at the
                                                             length limit, or closed before the end)

Used when Provider.wire == "chat_completions". When Provider.wire ==
"responses", chat falls back to translator_client (codex-server-style
routing for integration testing).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import requests


class ProviderError(RuntimeError):
    """Provider returned a non-2xx error."""


def list_models(base_url: str, api_key: str | None = None, timeout: float = 5.0) -> list[str]:
    """Hit /v1/models on the provider; return the list of model ids."""
    url = base_url.rstrip("/") + "/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = requests.get(url, headers=headers, timeout=timeout)
    if not resp.ok:
        raise ProviderError(f"GET {url} → HTTP {resp.status_code}: {resp.text[:200]}")
    payload = resp.json()
    # Standard OpenAI shape: {"object":"list", "data":[{"id":...}]}
    if isinstance(payload, dict) and "data" in payload:
        return [m.get("id", "") for m in payload["data"] if m.get("id")]
    # llama.cpp variant: {"models":[{"name":...}]}
    if isinstance(payload, dict) and "models" in payload:
        return [m.get("name") or m.get("model", "") for m in payload["models"] if m.get("name") or m.get("model")]
    return []


def build_chat_request(
    user_text: str,
    model: str,
    instructions: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a standard chat-completions request body. Messages of one role in a row are merged (see merge_same_role)."""
    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})
    for h in merge_same_role([*(history or []), {"role": "user", "text": user_text}]):
        messages.append({"role": h["role"], "content": h["text"]})
    return {"model": model, "messages": messages, "stream": True}


def merge_same_role(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``{role, text}`` entries with consecutive entries of one role joined by a blank line.

    A context line is a user message placed before the next user turn, and a reply that failed leaves two user turns in a
    row. Strict chat templates (llama.cpp with Gemma- or Mistral-style templates) reject roles that do not alternate.
    """
    merged: list[dict[str, Any]] = []
    for e in entries:
        if merged and merged[-1]["role"] == e["role"]:
            merged[-1] = {**merged[-1], "text": f"{merged[-1]['text']}\n\n{e['text']}"}
        else:
            merged.append(dict(e))
    return merged


def stream_chat_completions(
    base_url: str,
    request_body: dict[str, Any],
    api_key: str | None = None,
    timeout: float = 120.0,
) -> Iterator[dict[str, Any]]:
    """Stream a chat-completions response, yielding normalized event dicts.

    Emits the same shape as translator_client.stream_responses so chat
    dispatch can treat both paths uniformly:
      {"type": "text_delta", "delta": "..."}        (one per chunk)
      {"type": "completed", "text": "...",
       "finish_reason": "stop|length|tool_calls",
       "response_id": "..."}                        (terminal)
      {"type": "error", "message": "...",
       "text": "<partial>"}                         (terminal: an error chunk, finish_reason
                                                     "length", or a stream that closed mid-reply)

    Raises ProviderError on non-2xx.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    accumulated: list[str] = []
    response_id: str | None = None
    finish_reason: str | None = None  # None until a chunk says why the reply ended

    with requests.post(url, json=request_body, headers=headers, stream=True, timeout=timeout) as resp:
        if not resp.ok:
            body = resp.text
            raise ProviderError(f"POST {url} → HTTP {resp.status_code}: {body[:300]}")

        for raw in resp.iter_lines(decode_unicode=True):
            if raw is None:
                continue
            line = raw.strip()
            if not line.startswith("data:"):  # also skips blank keep-alive lines
                continue
            data = line[len("data:") :].strip()
            if not data:
                continue
            if data == "[DONE]":
                yield _terminal_event(accumulated, finish_reason or "stop", response_id)
                return
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            # An error body arrives as a chunk without choices. It ends the
            # reply: report it, never a (possibly empty) completed.
            message = _chunk_error(chunk)
            if message is not None:
                yield {"type": "error", "message": message, "text": "".join(accumulated)}
                return

            if response_id is None:
                rid = chunk.get("id")
                if isinstance(rid, str):
                    response_id = rid

            for choice in chunk.get("choices", []) or []:
                fr = choice.get("finish_reason")
                if isinstance(fr, str) and fr:  # an empty finish_reason mid-stream finishes nothing (second pass)
                    finish_reason = fr
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    accumulated.append(content)
                    yield {"type": "text_delta", "delta": content}

    # Closed without [DONE]. A chunk that said why the reply ended means the model finished (some servers omit [DONE]);
    # without one the connection dropped mid-reply, and the partial is an incomplete reply, never a completed one.
    yield _terminal_event(accumulated, finish_reason, response_id)


def _terminal_event(accumulated: list[str], finish_reason: str | None, response_id: str | None) -> dict[str, Any]:
    """The last event of a reply. Cut off at the token limit is an error with the partial text, as the Responses path's
    response.incomplete is: the reply is kept, marked incomplete, with a note saying why. No finish_reason at all means
    the stream stopped before the reply did."""
    text = "".join(accumulated)
    if finish_reason is None:
        return {"type": "error", "message": "stream ended before the reply completed", "text": text}
    if finish_reason == "length":
        return {"type": "error", "message": "reply cut off at the length limit (finish_reason length)", "text": text}
    return {"type": "completed", "text": text, "finish_reason": finish_reason, "response_id": response_id}


def _chunk_error(chunk: Any) -> str | None:
    """The message of an `{"error": ...}` chunk, or None for a normal chunk."""
    err = chunk.get("error") if isinstance(chunk, dict) else None
    if not err:
        return None
    message = err.get("message") if isinstance(err, dict) else None
    return str(message or err)


def resolve_api_key(api_key_env: str | None) -> str | None:
    """Read API key from env if env var name was configured."""
    if not api_key_env:
        return None
    val = os.environ.get(api_key_env)
    if not val:
        # Don't error — local providers (Ollama, llama.cpp) often need no key
        return None
    return val

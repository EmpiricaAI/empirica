"""empirica chat — Textual TUI app entry (Phase 1).

Standalone-usable skeleton. Phase 1 capabilities:
  - Header with mode badge + model + clock (placeholders for Phase 6)
  - Conversation scroll with rendered turns (user / agent_text / system)
  - Multi-line input (Enter submits, Shift+Enter newline)
  - Footer with key bindings
  - --feed sample.jsonl loads pre-baked conversation (no app-server dep)
  - --session-id RESUME loads an existing session from disk
  - All turns auto-persist to ~/.empirica/chat_sessions/{session_id}.jsonl

Phase 2 wires app-server WebSocket. Phase 3 wires translator event tap.
Phase 4 adds artifact cards. See CHAT.md for the full roadmap.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header

from empirica.core.chat.actions import (
    ActionError,
    extract_artifact_id,
    log_decision,
    log_finding,
    log_unknown,
)
from empirica.core.chat.openai_compat_client import (
    ProviderError,
    build_chat_request,
    list_models,
    resolve_api_key,
    stream_chat_completions,
)
from empirica.core.chat.providers import (
    Provider,
    ProviderRegistry,
    builtin_empirica_server_providers,
)
from empirica.core.chat.session import ChatSession, Turn, TurnKind, load_turns
from empirica.core.chat.translator_client import (
    TranslatorError,
    build_request_body,
    stream_responses,
)

from .chat.artifact_card import ArtifactCard
from .chat.conversation import ConversationScroll
from .chat.input import ChatInput
from .chat.statusline import RENDER_MODES, StatuslinePanel

REFRESH_SECONDS = 2.0


class ChatApp(App):
    """empirica chat — single-instance collaborative epistemic workspace."""

    CSS = """
    Screen { layout: vertical; }
    #chat-statusline { dock: top; }
    #chat-conversation { height: 1fr; }
    #chat-input { dock: bottom; }
    """

    STATUSLINE_REFRESH_SECONDS = 2.0

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+l", "clear_input", "Clear input"),
    ]

    TITLE = "empirica chat"
    SUB_TITLE = "🤖 conversational"  # Phase 6: dynamic from autonomy mode

    def __init__(
        self,
        feed_path: Path | None = None,
        session_id: str | None = None,
        feed_delay: float = 0.0,
        translator_url: str | None = None,
        model: str = "deepseek-chat",
        instructions: str | None = None,
        providers: list[Provider] | None = None,
    ) -> None:
        super().__init__()
        self.feed_path = feed_path
        self.session_id_to_resume = session_id
        self.feed_delay = feed_delay
        self.instructions = instructions
        self._session: ChatSession | None = None

        # Build provider registry. Priority: explicit --provider flags →
        # backwards-compat (--translator-url + --model) → builtin defaults
        # (empirica-server LAN). User can /provider NAME to switch at runtime.
        self.registry = ProviderRegistry()
        if providers:
            for p in providers:
                self.registry.add(p)
        if translator_url:
            self.registry.add(Provider(
                name="translator",
                base_url=translator_url,
                default_model=model,
                wire="responses",
            ))
        if not self.registry.providers:
            for p in builtin_empirica_server_providers():
                self.registry.add(p)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield StatuslinePanel()
        with Vertical():
            yield ConversationScroll(id="chat-conversation")
            yield ChatInput(id="chat-input")
        yield Footer()

    def on_mount(self) -> None:
        # Establish the chat session — resume if requested, else create new.
        if self.session_id_to_resume:
            self._session = ChatSession.load(self.session_id_to_resume)
            self._convo().render_existing(self._session.turns)
        else:
            self._session = ChatSession.create()

        # Reflect active provider:model in the header subtitle
        self._refresh_subtitle()

        # Tick the statusline panel so it stays current as the project's
        # epistemic state evolves (other instances logging artifacts, etc).
        try:
            sp = self.query_one(StatuslinePanel)
            sp.refresh_now()
            self.set_interval(self.STATUSLINE_REFRESH_SECONDS, sp.refresh_now)
        except Exception:  # noqa: BLE001 — pre-mount or test context
            pass

        # Optional: replay a sample feed (no app-server dep — useful for
        # reviewing the rendering UX before wiring upstream).
        if self.feed_path:
            self.run_worker(self._replay_feed(), thread=False)

        # Focus the input so the user can start typing immediately.
        self.query_one(ChatInput).focus()

    def _convo(self) -> ConversationScroll:
        return self.query_one("#chat-conversation", ConversationScroll)

    async def _replay_feed(self) -> None:
        """Stream turns from a feed file into the conversation."""
        assert self.feed_path is not None  # noqa: S101 — type narrowing
        assert self._session is not None  # noqa: S101 — type narrowing
        for turn in load_turns(self.feed_path):
            self._session.append(turn)
            self._convo().append_turn(turn)
            if self.feed_delay > 0:
                await asyncio.sleep(self.feed_delay)

    def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """User pressed Enter on a non-empty input."""
        assert self._session is not None  # noqa: S101 — type narrowing
        text = event.text

        # Phase 4: slash commands route to artifact creation rather than
        # the LLM. /finding, /decision, /unknown, /help — see _handle_slash.
        if text.startswith("/"):
            self._handle_slash(text)
            return

        turn = Turn.new(TurnKind.USER, text)
        self._session.append(turn)
        self._convo().append_turn(turn)

        active = self.registry.active()
        if active is None:
            self._emit_system("no provider configured — pass --provider NAME=URL or use builtin empirica-server defaults")
            return
        if not self.registry.active_model:
            self._emit_system(f"provider '{active.name}' has no active model — use /model NAME to set one")
            return

        # Dispatch to active provider in a worker thread (UI stays responsive).
        user_text = text
        self.run_worker(
            lambda: self._stream_agent_response(user_text),
            thread=True,
            exclusive=True,
            group="agent-stream",
        )

    def _stream_agent_response(self, user_text: str) -> None:
        """Worker thread: dispatch to active provider, stream deltas in."""
        assert self._session is not None  # noqa: S101 — type narrowing
        provider = self.registry.active()
        model = self.registry.active_model
        assert provider is not None and model is not None  # noqa: S101 — gated

        # Build request from prior history (exclude the just-appended user turn).
        history = [
            {"role": _to_translator_role(t.kind), "text": t.text}
            for t in self._session.turns[:-1]
            if t.kind in (TurnKind.USER, TurnKind.AGENT_TEXT)
        ]

        # Allocate a single AgentTurn we mutate as deltas arrive.
        agent_turn = Turn.new(TurnKind.AGENT_TEXT, "")
        self.call_from_thread(self._convo().append_turn, agent_turn)

        accumulated: list[str] = []
        try:
            event_stream = self._dispatch(provider, model, user_text, history)
            for ev in event_stream:
                etype = ev.get("type", "")
                if etype == "text_delta":
                    delta = ev.get("delta", "")
                    if delta:
                        accumulated.append(delta)
                        self.call_from_thread(
                            self._update_agent_turn,
                            agent_turn.turn_id,
                            "".join(accumulated),
                        )
                elif etype == "completed":
                    agent_turn.text = "".join(accumulated)
                    self._session.append(agent_turn)
        except (TranslatorError, ProviderError) as e:
            err = Turn.new(TurnKind.SYSTEM, f"{provider.name} error: {e}")
            self._session.append(err)
            self.call_from_thread(self._convo().append_turn, err)
        except Exception as e:  # noqa: BLE001 — surface any failure to chat
            err = Turn.new(TurnKind.SYSTEM, f"agent stream error: {type(e).__name__}: {e}")
            self._session.append(err)
            self.call_from_thread(self._convo().append_turn, err)

    def _dispatch(
        self,
        provider: Provider,
        model: str,
        user_text: str,
        history: list[dict[str, Any]],
    ):
        """Pick the right client based on provider.wire and yield normalized events."""
        if provider.wire == "responses":
            # Translator path: build Responses-format request, parse Responses-
            # format SSE, normalize to {text_delta, completed} dicts so the
            # streaming loop above can stay shape-agnostic.
            body = build_request_body(
                user_text=user_text,
                model=model,
                instructions=self.instructions,
                history=history,
            )
            for ev in stream_responses(provider.base_url, body):
                t = ev.get("type", "")
                if t == "response.output_text.delta":
                    yield {"type": "text_delta", "delta": ev.get("delta", "")}
                elif t == "response.completed":
                    # response.output[0].content[0].text holds the assembled text
                    text = ""
                    output = (ev.get("response") or {}).get("output") or []
                    for item in output:
                        for c in (item.get("content") or []):
                            if c.get("type") in ("output_text", "text"):
                                text += c.get("text", "")
                    yield {"type": "completed", "text": text}
            return

        # Default: direct chat-completions path (Ollama, llama.cpp, vLLM, …)
        body = build_chat_request(
            user_text=user_text,
            model=model,
            instructions=self.instructions,
            history=history,
        )
        api_key = resolve_api_key(provider.api_key_env)
        yield from stream_chat_completions(provider.base_url, body, api_key=api_key)

    def _update_agent_turn(self, turn_id: str, text: str) -> None:
        """Main-thread: update an existing agent turn widget's body in place."""
        try:
            widget = self.query_one(f"#turn-{turn_id[:8]}")
        except Exception:  # noqa: BLE001 — widget may have been removed
            return
        # Re-render via Static.update with the agent-style label
        widget.update(f"[b]agent:[/b] {text}")  # type: ignore[attr-defined]

    # ─── Phase 4: slash commands → artifact cards ─────────────────────

    def _handle_slash(self, text: str) -> None:
        """Parse and execute /finding, /decision, /unknown, /help."""
        assert self._session is not None  # noqa: S101 — type narrowing
        cmd, _, rest = text[1:].partition(" ")
        cmd = cmd.strip().lower()
        rest = rest.strip()

        if cmd in ("help", "?"):
            self._emit_system(
                "slash commands:\n"
                "  /providers             list configured providers\n"
                "  /provider NAME         switch active provider\n"
                "  /models                list models on active provider\n"
                "  /model NAME            set active model\n"
                "  /statusline [MODE]     cycle (or set) statusline mode: basic|default|learning|full\n"
                "  /finding TEXT          create a finding (renders as inline card)\n"
                "  /decision TEXT         create a decision\n"
                "  /unknown TEXT          create an unknown question\n"
                "  /help                  this list\n"
                "Anything else goes to the agent (current provider:model shown in header)."
            )
            return

        if cmd == "providers":
            lines = ["configured providers:"]
            active_name = self.registry.active_provider_name
            for name in self.registry.names():
                p = self.registry.get(name)
                marker = "▶" if name == active_name else " "
                lines.append(f"  {marker} {p.display() if p else name}")
            lines.append(f"\nactive: {self.registry.display_status()}")
            self._emit_system("\n".join(lines))
            return

        if cmd == "provider":
            if not rest:
                self._emit_system(f"/provider: missing NAME — current: {self.registry.display_status()}")
                return
            new = self.registry.set_active_provider(rest)
            if new is None:
                self._emit_system(f"unknown provider: {rest!r} (try /providers)")
                return
            self._refresh_subtitle()
            self._emit_system(f"switched to {self.registry.display_status()}")
            return

        if cmd == "models":
            self.run_worker(
                lambda: self._list_models_action(),
                thread=True, exclusive=False, group="provider-meta",
            )
            return

        if cmd == "model":
            if not rest:
                self._emit_system(f"/model: missing NAME — current: {self.registry.display_status()}")
                return
            if self.registry.set_active_model(rest):
                self._refresh_subtitle()
                self._emit_system(f"model set to {rest} on provider {self.registry.active_provider_name}")
            else:
                self._emit_system("/model: no active provider — use /provider NAME first")
            return

        if cmd == "statusline":
            try:
                sp = self.query_one(StatuslinePanel)
            except Exception:  # noqa: BLE001
                self._emit_system("/statusline: panel not mounted")
                return
            if rest:
                if sp.set_mode(rest):
                    self._emit_system(f"statusline mode → {rest}")
                else:
                    self._emit_system(f"unknown statusline mode: {rest!r} (valid: {', '.join(RENDER_MODES)})")
            else:
                new_mode = sp.cycle_mode()
                self._emit_system(f"statusline mode → {new_mode}  (cycle through {', '.join(RENDER_MODES)})")
            return

        if not rest:
            self._emit_system(f"/{cmd}: missing text — usage: /{cmd} <description>")
            return

        if cmd == "finding":
            self.run_worker(
                lambda: self._create_artifact("finding", rest),
                thread=True, exclusive=False, group="artifact-create",
            )
            return
        if cmd == "decision":
            self.run_worker(
                lambda: self._create_artifact("decision", rest),
                thread=True, exclusive=False, group="artifact-create",
            )
            return
        if cmd == "unknown":
            self.run_worker(
                lambda: self._create_artifact("unknown", rest),
                thread=True, exclusive=False, group="artifact-create",
            )
            return

        self._emit_system(f"unknown slash command: /{cmd} — try /help")

    def _create_artifact(self, artifact_type: str, text: str) -> None:
        """Worker thread: invoke empirica CLI, render the artifact card."""
        assert self._session is not None  # noqa: S101 — type narrowing
        try:
            if artifact_type == "finding":
                resp = log_finding(text)
            elif artifact_type == "decision":
                resp = log_decision(text)
            elif artifact_type == "unknown":
                resp = log_unknown(text)
            else:
                self.call_from_thread(
                    self._emit_system, f"artifact type not yet supported: {artifact_type}"
                )
                return
        except ActionError as e:
            self.call_from_thread(self._emit_system, f"artifact creation failed: {e}")
            return

        artifact_id = extract_artifact_id(resp)
        meta: dict[str, object] = {
            "artifact_type": artifact_type,
            "artifact_id": artifact_id,
        }
        if artifact_type in ("finding", "unknown"):
            meta["impact"] = 0.5  # default; finer impact via slash flags is Phase 4b
        if artifact_type == "decision":
            meta["reversibility"] = "exploratory"

        turn = Turn.new(TurnKind.EPISTEMIC_ACTION, text, metadata=meta)
        self._session.append(turn)
        self.call_from_thread(self._convo().append_turn, turn)

    def _emit_system(self, text: str) -> None:
        """Append a SystemTurn from any thread (uses call_from_thread if needed)."""
        assert self._session is not None  # noqa: S101 — type narrowing
        turn = Turn.new(TurnKind.SYSTEM, text)
        self._session.append(turn)
        # If we're already on the main thread, append directly; otherwise
        # call_from_thread. Textual's threading model makes both safe enough.
        try:
            self._convo().append_turn(turn)
        except Exception:  # noqa: BLE001 — thread context unclear
            self.call_from_thread(self._convo().append_turn, turn)

    def _refresh_subtitle(self) -> None:
        """Reflect active provider:model in the header subtitle."""
        try:
            self.sub_title = self.registry.display_status()
        except Exception:  # noqa: BLE001 — pre-mount or test context
            pass

    def _list_models_action(self) -> None:
        """Worker: query the active provider's /v1/models endpoint, render a system note."""
        provider = self.registry.active()
        if provider is None:
            self.call_from_thread(self._emit_system, "/models: no active provider")
            return
        try:
            api_key = resolve_api_key(provider.api_key_env)
            models = list_models(provider.base_url, api_key=api_key)
        except Exception as e:  # noqa: BLE001 — surface to chat
            self.call_from_thread(
                self._emit_system,
                f"/models: error fetching from {provider.name}: {type(e).__name__}: {e}",
            )
            return
        if not models:
            msg = f"/models: provider {provider.name} returned no models"
        else:
            current = self.registry.active_model
            lines = [f"models on {provider.name}:"]
            for m in models:
                marker = "▶" if m == current else " "
                lines.append(f"  {marker} {m}")
            msg = "\n".join(lines)
        self.call_from_thread(self._emit_system, msg)

    def on_artifact_card_action_invoked(self, event: ArtifactCard.ActionInvoked) -> None:
        """Bubble from per-card buttons. Phase 4 v1: emit a system note as ack.

        Phase 5+ wires this to real action invocations:
          - finding.confirm → empirica finding-log (with link to original)
          - unknown.resolve → empirica unknown-resolve
          - *.discuss → inject into next agent turn as system message
          - *.pin → write to chat_pinned_{session_id}.json
        """
        msg = (
            f"action: {event.artifact_type}.{event.action} "
            f"on artifact {(event.artifact_id or 'unknown')[:8]} "
            f"(turn {event.turn_id[:8]}) — wiring is Phase 5+"
        )
        self._emit_system(msg)

    def action_clear_input(self) -> None:
        try:
            self.query_one(ChatInput).load_text("")
        except Exception:  # noqa: S110 — clear is best-effort UI op
            pass


def _to_translator_role(kind: TurnKind) -> str:
    """Map a CIF turn kind to the role string the translator expects."""
    if kind == TurnKind.USER:
        return "user"
    if kind == TurnKind.AGENT_TEXT:
        return "assistant"
    return "user"  # safe fallback


def run_chat(
    feed_path: Path | None = None,
    session_id: str | None = None,
    feed_delay: float = 0.0,
    translator_url: str | None = None,
    model: str = "deepseek-chat",
    instructions: str | None = None,
    providers: list[Provider] | None = None,
) -> int:
    app = ChatApp(
        feed_path=feed_path,
        session_id=session_id,
        feed_delay=feed_delay,
        translator_url=translator_url,
        model=model,
        instructions=instructions,
        providers=providers,
    )
    app.run()
    return 0

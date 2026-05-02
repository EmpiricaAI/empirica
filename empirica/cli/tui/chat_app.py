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
from empirica.core.chat.slash import known_commands, render_help
from empirica.core.chat.system_prompt import (
    AUTONOMY_DEFAULT,
    AUTONOMY_MODES,
    MODE_BADGES,
    preview_lines,
    render_system_prompt,
)
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
        Binding("ctrl+m", "model_selector", "Model selector"),
    ]

    TITLE = "empirica chat"
    SUB_TITLE = MODE_BADGES[AUTONOMY_DEFAULT]  # overwritten in _refresh_subtitle

    def __init__(
        self,
        feed_path: Path | None = None,
        session_id: str | None = None,
        feed_delay: float = 0.0,
        translator_url: str | None = None,
        model: str = "deepseek-chat",
        instructions: str | None = None,
        providers: list[Provider] | None = None,
        autonomy_mode: str = AUTONOMY_DEFAULT,
        enable_system_prompt: bool = True,
    ) -> None:
        super().__init__()
        self.feed_path = feed_path
        self.session_id_to_resume = session_id
        self.feed_delay = feed_delay
        # User-supplied --system text; combined with the empirica system
        # prompt at on_mount time. Stored separately so we can rebuild on
        # /provider or /model switches.
        self.user_instructions = instructions
        self.instructions: str | None = None
        self.autonomy_mode = autonomy_mode
        self.enable_system_prompt = enable_system_prompt
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

        # Compose the system prompt and append it as turn 0 so the user
        # can see what the AI was told. The full text is what gets sent
        # as LLM instructions; the rendered turn shows a short preview.
        # Resumed sessions skip this — the original turn 0 is already in
        # the session's turn list and instructions persist from prior run.
        if (
            self.enable_system_prompt
            and self.session_id_to_resume is None
        ):
            self._install_system_prompt()
        elif self.user_instructions:
            # System prompt disabled but user supplied --system: pass
            # their text straight through as instructions.
            self.instructions = self.user_instructions

        # Reflect active provider:model + autonomy mode in the subtitle
        self._refresh_subtitle()

        # Tick the statusline panel so it stays current as the project's
        # epistemic state evolves (other instances logging artifacts, etc).
        try:
            sp = self.query_one(StatuslinePanel)
            sp.refresh_now()
            self.set_interval(self.STATUSLINE_REFRESH_SECONDS, sp.refresh_now)
        except Exception:  # noqa: S110 — best-effort statusline tick (pre-mount or test context)
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
        # Phase 14: default source='intuition'; Phase 2b will flip to
        # 'search' when tool-call activity is observed mid-stream.
        agent_turn = Turn.new(
            TurnKind.AGENT_TEXT, "", metadata={"source": "intuition"},
        )
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
        except Exception as e:
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
        """Main-thread: update an existing agent turn widget's body in place.

        Phase 14: re-render through AgentTurn._format_body when the
        widget is one — preserves the source badge across streaming
        deltas. Falls back to plain prefix for non-AgentTurn widgets.
        """
        try:
            widget = self.query_one(f"#turn-{turn_id[:8]}")
        except Exception:
            return
        from empirica.cli.tui.chat.turn import AgentTurn
        if isinstance(widget, AgentTurn):
            widget.turn.text = text
            widget.update(widget._format_body())
        else:
            widget.update(f"[b]agent:[/b] {text}")  # type: ignore[attr-defined]

    # ─── Phase 16: slash commands ─────────────────────────────────────

    def _handle_slash(self, text: str) -> None:
        """Parse and dispatch a slash command via SLASH_HANDLERS table."""
        assert self._session is not None  # noqa: S101 — type narrowing
        cmd, _, rest = text[1:].partition(" ")
        cmd = cmd.strip().lower()
        rest = rest.strip()

        if cmd in ("?", ""):  # legacy aliases for /help
            cmd = "help"

        handler = self.SLASH_HANDLERS.get(cmd)
        if handler is None:
            if cmd in known_commands():
                self._emit_system(f"/{cmd}: handler missing — internal error, please report")
            else:
                self._emit_system(f"unknown slash command: /{cmd} — try /help")
            return
        handler(self, rest)

    # ─── Per-command handlers (declared on class for SLASH_HANDLERS lookup) ──

    def _slash_help(self, rest: str) -> None:
        debug = rest.strip().lower() == "debug"
        self._emit_system(render_help(debug=debug))

    def _slash_plan(self, _rest: str) -> None:
        """Show open goals + recent transactions for the project."""
        self.run_worker(
            lambda: self._plan_action(),
            thread=True, exclusive=False, group="empirica-meta",
        )

    def _slash_autonomy(self, rest: str) -> None:
        if not rest:
            self._emit_system(
                f"/autonomy: missing MODE — current: {self.autonomy_mode}  "
                f"(valid: {', '.join(AUTONOMY_MODES)})"
            )
            return
        mode = rest.strip().lower()
        if mode not in AUTONOMY_MODES:
            self._emit_system(
                f"unknown autonomy mode: {rest!r} (valid: {', '.join(AUTONOMY_MODES)})"
            )
            return
        if mode == self.autonomy_mode:
            self._emit_system(f"autonomy already {mode}")
            return
        old = self.autonomy_mode
        self.autonomy_mode = mode
        # Re-render the system prompt under the new mode (if enabled).
        if self.enable_system_prompt:
            provider = self.registry.active()
            provider_name = provider.name if provider else "(none)"
            model_name = self.registry.active_model or "(none)"
            self.instructions = render_system_prompt(
                provider=provider_name,
                model=model_name,
                autonomy_mode=mode,
                user_instructions=self.user_instructions,
            )
        self._refresh_subtitle()
        self._emit_system(f"autonomy: {old} → {mode}")

    def _slash_providers(self, _rest: str) -> None:
        lines = ["configured providers:"]
        active_name = self.registry.active_provider_name
        for name in self.registry.names():
            p = self.registry.get(name)
            marker = "▶" if name == active_name else " "
            lines.append(f"  {marker} {p.display() if p else name}")
        lines.append(f"\nactive: {self.registry.display_status()}")
        self._emit_system("\n".join(lines))

    def _slash_provider(self, rest: str) -> None:
        if not rest:
            self._emit_system(f"/provider: missing NAME — current: {self.registry.display_status()}")
            return
        if self.registry.set_active_provider(rest) is None:
            self._emit_system(f"unknown provider: {rest!r} (try /providers)")
            return
        self._refresh_subtitle()
        self._emit_system(f"switched to {self.registry.display_status()}")

    def _slash_models(self, _rest: str) -> None:
        self.run_worker(
            lambda: self._list_models_action(),
            thread=True, exclusive=False, group="provider-meta",
        )

    def _slash_model(self, rest: str) -> None:
        if not rest:
            self._emit_system(f"/model: missing NAME — current: {self.registry.display_status()}")
            return
        if self.registry.set_active_model(rest):
            self._refresh_subtitle()
            self._emit_system(f"model set to {rest} on provider {self.registry.active_provider_name}")
        else:
            self._emit_system("/model: no active provider — use /provider NAME first")

    def _slash_statusline(self, rest: str) -> None:
        try:
            sp = self.query_one(StatuslinePanel)
        except Exception:
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

    def _slash_artifact(self, artifact_type: str, rest: str) -> None:
        """Shared handler body for /finding | /decision | /unknown."""
        if not rest:
            self._emit_system(
                f"/{artifact_type}: missing text — usage: /{artifact_type} <description>"
            )
            return
        self.run_worker(
            lambda: self._create_artifact(artifact_type, rest),
            thread=True, exclusive=False, group="artifact-create",
        )

    def _slash_finding(self, rest: str) -> None:
        self._slash_artifact("finding", rest)

    def _slash_decision(self, rest: str) -> None:
        self._slash_artifact("decision", rest)

    def _slash_unknown(self, rest: str) -> None:
        self._slash_artifact("unknown", rest)

    def _slash_batch(self, rest: str) -> None:
        """/batch PATH — log a batch artifact graph from a JSON file."""
        if not rest:
            self._emit_system("/batch: missing PATH — usage: /batch <path-to-json>")
            return
        path = rest.strip()
        self.run_worker(
            lambda: self._batch_log_action(path),
            thread=True, exclusive=False, group="artifact-create",
        )

    def _slash_resolve_batch(self, rest: str) -> None:
        """/resolve-batch ID1 ID2 …"""
        ids = [i.strip() for i in rest.split() if i.strip()]
        if not ids:
            self._emit_system("/resolve-batch: missing IDs — usage: /resolve-batch ID1 [ID2 …]")
            return
        self.run_worker(
            lambda: self._batch_resolve_action(ids),
            thread=True, exclusive=False, group="artifact-create",
        )

    def _slash_delete_batch(self, rest: str) -> None:
        """/delete-batch ID1 ID2 …"""
        ids = [i.strip() for i in rest.split() if i.strip()]
        if not ids:
            self._emit_system("/delete-batch: missing IDs — usage: /delete-batch ID1 [ID2 …]")
            return
        self.run_worker(
            lambda: self._batch_delete_action(ids),
            thread=True, exclusive=False, group="artifact-create",
        )

    def _batch_log_action(self, path: str) -> None:
        from empirica.core.chat.actions import (
            ActionError as _ActionError,
        )
        from empirica.core.chat.actions import (
            log_artifacts_from_file,
        )
        try:
            resp = log_artifacts_from_file(path)
        except _ActionError as e:
            self.call_from_thread(self._emit_system, f"/batch: {e}")
            return
        created = resp.get("nodes_created") if isinstance(resp, dict) else None
        edges = resp.get("edges_wired") if isinstance(resp, dict) else None
        errors = resp.get("errors") if isinstance(resp, dict) else None
        bits = []
        if isinstance(created, int):
            bits.append(f"{created} nodes")
        if isinstance(edges, int):
            bits.append(f"{edges} edges")
        msg = f"/batch: created {', '.join(bits)}" if bits else "/batch: completed"
        if errors:
            msg += f" — errors: {errors}"
        self.call_from_thread(self._emit_system, msg)

    def _batch_resolve_action(self, ids: list[str]) -> None:
        from empirica.core.chat.actions import (
            ActionError as _ActionError,
        )
        from empirica.core.chat.actions import (
            resolve_artifacts_batch,
        )
        try:
            resp = resolve_artifacts_batch(ids)
        except _ActionError as e:
            self.call_from_thread(self._emit_system, f"/resolve-batch: {e}")
            return
        n = resp.get("resolved") if isinstance(resp, dict) else None
        msg = f"/resolve-batch: resolved {n} of {len(ids)} unknowns" if isinstance(n, int) else f"/resolve-batch: completed ({len(ids)} IDs)"
        self.call_from_thread(self._emit_system, msg)

    def _batch_delete_action(self, ids: list[str]) -> None:
        from empirica.core.chat.actions import (
            ActionError as _ActionError,
        )
        from empirica.core.chat.actions import (
            delete_artifacts_batch,
        )
        try:
            resp = delete_artifacts_batch(ids)
        except _ActionError as e:
            self.call_from_thread(self._emit_system, f"/delete-batch: {e}")
            return
        n = resp.get("deleted") if isinstance(resp, dict) else None
        msg = f"/delete-batch: deleted {n} of {len(ids)} artifacts" if isinstance(n, int) else f"/delete-batch: completed ({len(ids)} IDs)"
        self.call_from_thread(self._emit_system, msg)

    def _plan_action(self) -> None:
        """Worker thread: query empirica goals-list + format for chat."""
        from empirica.core.chat.actions import ActionError, _run_cli
        try:
            data = _run_cli(["goals-list"])
        except ActionError as e:
            self.call_from_thread(self._emit_system, f"/plan: empirica CLI error: {e}")
            return
        goals = data if isinstance(data, list) else data.get("goals", [])
        open_goals = [g for g in goals if isinstance(g, dict) and not g.get("is_completed")]
        if not open_goals:
            self.call_from_thread(
                self._emit_system,
                "/plan: no open goals — `empirica goals-list` is empty for this project",
            )
            return
        lines = [f"open goals ({len(open_goals)}):"]
        for g in open_goals[:15]:
            obj = (g.get("objective") or "").replace("\n", " ").strip()
            if len(obj) > 100:
                obj = obj[:97] + "..."
            status = g.get("status", "?")
            pct = g.get("progress_pct")
            pct_str = f" {int(pct)}%" if isinstance(pct, (int, float)) else ""
            lines.append(f"  • [{status}{pct_str}] {obj}")
        if len(open_goals) > 15:
            lines.append(
                f"  … and {len(open_goals) - 15} more (use `empirica goals-list` for full)"
            )
        self.call_from_thread(self._emit_system, "\n".join(lines))

    # Dispatch table — populated below the class body so methods exist.
    SLASH_HANDLERS: ClassVar[dict[str, Any]] = {}

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
        except Exception:
            self.call_from_thread(self._convo().append_turn, turn)

    def _refresh_subtitle(self) -> None:
        """Reflect active provider:model + autonomy mode in the subtitle."""
        try:
            badge = MODE_BADGES.get(self.autonomy_mode, self.autonomy_mode)
            self.sub_title = f"{badge}  ·  {self.registry.display_status()}"
        except Exception:  # noqa: S110 — best-effort subtitle refresh (pre-mount or test context)
            pass

    def _install_system_prompt(self) -> None:
        """Render system prompt → set self.instructions → append turn 0."""
        assert self._session is not None  # noqa: S101 — type narrowing
        provider = self.registry.active()
        provider_name = provider.name if provider else "(none)"
        model = self.registry.active_model or "(none)"
        prompt = render_system_prompt(
            provider=provider_name,
            model=model,
            autonomy_mode=self.autonomy_mode,
            user_instructions=self.user_instructions,
        )
        self.instructions = prompt
        # Visual turn 0 — short preview, full prompt available via /help debug
        # (Phase 16). Stored as SYSTEM so the LLM history filter naturally
        # excludes it from the per-request context.
        preview = preview_lines(prompt, max_lines=3)
        turn = Turn.new(
            TurnKind.SYSTEM,
            f"system prompt installed ({MODE_BADGES.get(self.autonomy_mode, self.autonomy_mode)})\n{preview}",
            metadata={"system_prompt": True, "autonomy_mode": self.autonomy_mode},
        )
        self._session.append(turn)
        self._convo().append_turn(turn)

    def _list_models_action(self) -> None:
        """Worker: query the active provider's /v1/models endpoint, render a system note."""
        provider = self.registry.active()
        if provider is None:
            self.call_from_thread(self._emit_system, "/models: no active provider")
            return
        try:
            api_key = resolve_api_key(provider.api_key_env)
            models = list_models(provider.base_url, api_key=api_key)
        except Exception as e:
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

    def action_model_selector(self) -> None:
        """Open the Phase 12 modal model selector (Ctrl+M)."""
        from empirica.cli.tui.chat.model_selector import ModelSelectorModal

        def _on_dismiss(model_name: str | None) -> None:
            if model_name and self.registry.set_active_model(model_name):
                self._refresh_subtitle()
                self._emit_system(
                    f"model set to {model_name} on provider {self.registry.active_provider_name}"
                )
        self.push_screen(ModelSelectorModal(self.registry), _on_dismiss)


# Populate the dispatch table now that ChatApp's methods are defined.
# Each value is an unbound method; the dispatcher passes (self, rest).
ChatApp.SLASH_HANDLERS = {
    "help": ChatApp._slash_help,
    "model": ChatApp._slash_model,
    "plan": ChatApp._slash_plan,
    "autonomy": ChatApp._slash_autonomy,
    "providers": ChatApp._slash_providers,
    "provider": ChatApp._slash_provider,
    "models": ChatApp._slash_models,
    "statusline": ChatApp._slash_statusline,
    "finding": ChatApp._slash_finding,
    "decision": ChatApp._slash_decision,
    "unknown": ChatApp._slash_unknown,
    "batch": ChatApp._slash_batch,
    "resolve-batch": ChatApp._slash_resolve_batch,
    "delete-batch": ChatApp._slash_delete_batch,
}


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
    autonomy_mode: str = AUTONOMY_DEFAULT,
    enable_system_prompt: bool = True,
) -> int:
    app = ChatApp(
        feed_path=feed_path,
        session_id=session_id,
        feed_delay=feed_delay,
        translator_url=translator_url,
        model=model,
        instructions=instructions,
        providers=providers,
        autonomy_mode=autonomy_mode,
        enable_system_prompt=enable_system_prompt,
    )
    app.run()
    return 0

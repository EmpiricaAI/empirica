"""Modal arrow-key model selector for empirica chat (Phase 12).

Up/down arrows cycle, Enter selects + switches, Esc cancels. Reuses
empirica.core.chat.openai_compat_client.list_models for the fetch
(same path /models slash command uses) and ProviderRegistry to apply
the switch. Bound to Ctrl+M at the ChatApp level.

Design note: Textual's OptionList already handles up/down/Enter
natively, so the modal is mostly composition: a styled container
with one OptionList + a thin border + status footer. No manual key
handling needed.
"""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from empirica.core.chat.openai_compat_client import (
    ProviderError,
    list_models,
    resolve_api_key,
)
from empirica.core.chat.providers import ProviderRegistry


class ModelSelectorModal(ModalScreen[str | None]):
    """Modal that lets the user pick a model on the active provider.

    Returns the selected model name (or None if cancelled). Caller is
    expected to call registry.set_active_model on the result.
    """

    DEFAULT_CSS = """
    ModelSelectorModal {
        align: center middle;
    }
    #model-modal-container {
        width: 60%;
        max-width: 80;
        max-height: 80%;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }
    #model-modal-title {
        text-style: bold;
        color: $accent;
        margin-bottom: 1;
    }
    #model-modal-list {
        height: auto;
        max-height: 20;
    }
    #model-modal-footer {
        color: $text-muted;
        text-style: italic;
        margin-top: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "dismiss(None)", "Cancel"),
    ]

    def __init__(self, registry: ProviderRegistry) -> None:
        super().__init__()
        self.registry = registry

    def compose(self) -> ComposeResult:
        provider = self.registry.active()
        provider_name = provider.name if provider else "(none)"
        with Vertical(id="model-modal-container"):
            yield Label(
                f"Select model on provider [b]{provider_name}[/b]",
                id="model-modal-title",
            )
            yield OptionList(
                Option("(loading…)", id="__loading__"),
                id="model-modal-list",
            )
            yield Label("↑/↓ navigate · Enter select · Esc cancel", id="model-modal-footer")

    def on_mount(self) -> None:
        # Fetch models in a worker so the modal opens instantly,
        # then populate the OptionList when results arrive.
        self.run_worker(self._populate_models, thread=True, exclusive=True, group="model-modal")
        # Auto-focus the list so arrow keys work immediately
        try:
            self.query_one(OptionList).focus()
        except Exception:  # noqa: S110 — best-effort focus (pre-mount fallback)
            pass

    def _populate_models(self) -> None:
        provider = self.registry.active()
        models: list[str] = []
        error: str | None = None
        if provider is None:
            error = "no active provider"
        else:
            try:
                api_key = resolve_api_key(provider.api_key_env)
                models = list_models(provider.base_url, api_key=api_key)
            except ProviderError as e:
                error = f"{provider.name}: {e}"
            except Exception as e:
                error = f"{provider.name}: {type(e).__name__}: {e}"
        self.call_from_thread(self._render_models, models, error)

    def _render_models(
        self, models: list[str], error: str | None,
    ) -> None:
        try:
            ol = self.query_one(OptionList)
        except Exception:
            return
        ol.clear_options()
        if error:
            ol.add_option(Option(f"⚠ {error}", id="__error__"))
            return
        if not models:
            ol.add_option(Option("(no models returned)", id="__empty__"))
            return
        current = self.registry.active_model
        for m in models:
            marker = "▶ " if m == current else "  "
            ol.add_option(Option(f"{marker}{m}", id=m))
        # Highlight the current model if present
        if current in models:
            try:
                ol.highlighted = models.index(current)
            except (ValueError, AttributeError):
                pass

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        """Enter pressed on an option — dismiss with the model name."""
        opt_id = event.option.id
        if opt_id is None or opt_id.startswith("__"):
            # Loading/error/empty placeholders don't dispatch
            return
        self.dismiss(opt_id)

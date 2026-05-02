"""Tests for empirica.cli.tui.chat.model_selector — Phase 12.

We don't drive a full Textual pilot here — that path is exercised by
manual smoke runs of `empirica chat`. These tests cover the
construction surface and the dispatcher-relevant data flow.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from empirica.cli.tui.chat.model_selector import ModelSelectorModal
from empirica.core.chat.providers import Provider, ProviderRegistry


@pytest.fixture
def registry():
    r = ProviderRegistry()
    r.add(Provider(name="ollama", base_url="http://localhost:11434/v1", default_model="qwen3.5"))
    r.add(Provider(name="deepseek", base_url="https://api.deepseek.com/v1", default_model="deepseek-chat"))
    return r


class TestModelSelectorModalConstruction:
    def test_constructs_with_registry(self, registry):
        modal = ModelSelectorModal(registry)
        assert modal.registry is registry

    def test_has_escape_binding(self, registry):
        modal = ModelSelectorModal(registry)
        # BINDINGS is a class attr listing Binding objects
        keys = {b.key for b in modal.BINDINGS}
        assert "escape" in keys


class TestModelSelectorPopulate:
    """Test the data-rendering path with a mocked OptionList."""

    def setup_method(self):
        # Build a real registry so set_active_model paths exist
        self.registry = ProviderRegistry()
        self.registry.add(Provider(
            name="ollama", base_url="http://localhost:11434/v1",
            default_model="qwen3.5",
        ))
        self.modal = ModelSelectorModal(self.registry)
        # Bypass real Textual: stub query_one to return a mock OptionList
        self.mock_list = MagicMock()
        self.modal.query_one = MagicMock(return_value=self.mock_list)  # type: ignore[method-assign]

    def test_render_models_with_results(self):
        self.modal._render_models(["qwen3.5", "llama3.1", "mistral"], None)
        self.mock_list.clear_options.assert_called_once()
        # 3 options added (one per model)
        assert self.mock_list.add_option.call_count == 3

    def test_render_models_with_error(self):
        self.modal._render_models([], "connection refused")
        self.mock_list.clear_options.assert_called_once()
        # One option added — the error placeholder
        assert self.mock_list.add_option.call_count == 1
        added = self.mock_list.add_option.call_args[0][0]
        assert "⚠" in added.prompt
        assert "connection refused" in added.prompt

    def test_render_models_empty_list(self):
        self.modal._render_models([], None)
        self.mock_list.clear_options.assert_called_once()
        assert self.mock_list.add_option.call_count == 1
        added = self.mock_list.add_option.call_args[0][0]
        assert "no models returned" in added.prompt

    def test_render_models_marks_current(self):
        self.registry.set_active_model("qwen3.5")
        self.modal._render_models(["llama3.1", "qwen3.5", "mistral"], None)
        # The qwen3.5 option should have ▶ marker prefix
        added_prompts = [
            call.args[0].prompt for call in self.mock_list.add_option.call_args_list
        ]
        qwen_prompt = next(p for p in added_prompts if "qwen3.5" in p)
        assert "▶" in qwen_prompt


class TestModelSelectorDismissHandling:
    def test_placeholder_ids_dont_dispatch(self, registry):
        from textual.widgets.option_list import Option
        modal = ModelSelectorModal(registry)
        modal.dismiss = MagicMock()  # type: ignore[method-assign]
        # Simulate event for a placeholder
        evt = MagicMock()
        evt.option = Option("loading", id="__loading__")
        modal.on_option_list_option_selected(evt)
        modal.dismiss.assert_not_called()

    def test_real_model_id_dispatches(self, registry):
        from textual.widgets.option_list import Option
        modal = ModelSelectorModal(registry)
        modal.dismiss = MagicMock()  # type: ignore[method-assign]
        evt = MagicMock()
        evt.option = Option("qwen3.5", id="qwen3.5")
        modal.on_option_list_option_selected(evt)
        modal.dismiss.assert_called_once_with("qwen3.5")

    def test_none_id_dont_dispatch(self, registry):
        from textual.widgets.option_list import Option
        modal = ModelSelectorModal(registry)
        modal.dismiss = MagicMock()  # type: ignore[method-assign]
        evt = MagicMock()
        evt.option = Option("no-id-option")  # id defaults to None
        modal.on_option_list_option_selected(evt)
        modal.dismiss.assert_not_called()

"""The tool router's keyword lists match words, not substrings (goal 546e7c33).

Every prompt runs through these lists, and a hit puts an agent or workflow suggestion into the context. As substrings,
"auth" matched "author", "ux" matched "linux", "layer" matched "player", "review" matched "preview", "check" matched
"checkout", and "try"/"fix" matched "registry"/"prefix". A suggestion that fires on ordinary words is noise the reader
learns to skip. Intended stems (optimiz-, calibrat-, sanitiz-) still match their word families.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/tool-router.py"


@pytest.fixture(scope="module")
def router():
    spec = importlib.util.spec_from_file_location("tool_router_words", _HOOK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _agents(router, text):
    return [m["name"] for m in router.match_agents(text.lower())]


@pytest.mark.parametrize(
    "text",
    [
        "update the author and authority fields in the citation",
        "fix the linux path for the flux build",
        "rename player.py and the explorer panel",
        "rename pindex.py in the cpufreq folder",  # index inside a word; "indexer" is its family since -er
    ],
)
def test_words_that_only_contain_a_keyword_match_no_agent(router, text):
    assert _agents(router, text) == []


@pytest.mark.parametrize(
    "text",
    [
        "preview the checkout page",
        "the checklist template",
        "add a prefix to the registry entry",
    ],
)
def test_words_that_only_contain_a_workflow_keyword_are_not_workflow_tasks(router, text):
    lower = text.lower()
    assert not router.is_investigation_task(lower)
    assert not router.is_epistemic_task(lower)


@pytest.mark.parametrize(
    "text, agent",
    [
        ("check the auth flow for the OAuth callback", "empirica:security"),
        ("sanitizing user input before the query", "empirica:security"),
        ("encrypted tokens at rest", "empirica:security"),
        ("optimizing cache latency", "empirica:performance"),
        ("the queries are slow and need an index", "empirica:performance"),
        ("untangle the dependencies between components", "empirica:architecture"),
        ("is the ux of the error message clear", "empirica:ux"),
    ],
)
def test_the_keywords_and_their_word_families_still_match(router, text, agent):
    assert agent in _agents(router, text)


@pytest.mark.parametrize(
    "text",
    ["investigating why the listener stops", "please review this", "check the calibration drift"],
)
def test_workflow_words_still_match(router, text):
    lower = text.lower()
    assert router.is_investigation_task(lower) or router.is_epistemic_task(lower)


def test_the_dead_end_hint_words_match_words(router):
    words = ["try", "attempt", "approach", "workaround", "fix"]
    assert router._keyword_hits("add a prefix to the registry entry for the fixture", words) == 0
    assert router._keyword_hits("try the workaround, then fixing the retry is next", words) == 3
    assert router.is_investigation_task("open the explorer panel") is False


@pytest.mark.parametrize(
    "text, agent",
    [
        ("authorize the admin endpoint", "empirica:security"),
        ("authenticate users against the directory", "empirica:security"),
        ("an attacker could replay the token", "empirica:security"),
        ("the page is slower since the change", "empirica:performance"),
    ],
)
def test_word_families_the_substring_match_used_to_catch(router, text, agent):
    assert agent in _agents(router, text)

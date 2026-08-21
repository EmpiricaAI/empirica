"""The prompt gates must match what the user SAID, not what the user PASTED.

`tool-router.py` runs on every UserPromptSubmit. Two of its blocks are lexical
matchers over natural-language prompt text, and both matched anywhere in the raw
prompt with no normalisation. Measured over **12,181 real prompts** from this
box's transcripts:

    investigation-proportionality   fired on 20.3%
      of those fires, 53% triggered past the first 200 characters
      on prompts of 10k+ characters: 561 of 563 fires (99.6%) did
    AAP hedge detection             fired on 15.1%, 55% of them past char 200

Those deep matches are pasted logs, peer messages and command output. A
proportionality hit **arms a Sentinel budget that later DENIES tool calls**, so
the word "maybe" inside material the user quoted was buying a runtime denial.

Restricting to the user's own framing takes the 10k+ case from 561 fires to 20.

The second half is the false negative, which no list can fix: roughly one short
prompt in seven that hands over a hypothesis does it in words the patterns do not
contain — *"They do exist &lt;path&gt;"*, *"how can we be sure that…"*. So the
breadth moved to a terse semantic pointer (the shape the EPP block already uses
in this same file), and the patterns now only decide when to arm the teeth.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROUTER = (
    Path(__file__).resolve().parent.parent
    / "empirica"
    / "plugins"
    / "claude-code-integration"
    / "hooks"
    / "tool-router.py"
)


@pytest.fixture(scope="module")
def tr():
    spec = importlib.util.spec_from_file_location("tool_router_under_test", ROUTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HYPOTHESIS = "I think it might be the config path"


# ── the normaliser ───────────────────────────────────────────────────────────


def test_a_fenced_paste_is_not_the_users_words(tr):
    """The measured defect: a marker inside pasted material armed the budget."""
    prompt = f"here is the log:\n```\n{HYPOTHESIS}\n```\nwhat now?"
    assert HYPOTHESIS not in tr.user_own_words(prompt)
    assert tr.build_investigation_proportionality_check(prompt) is None


def test_quoted_and_log_shaped_lines_are_dropped(tr):
    prompt = "peer said:\n> I think the resolver is wrong\napp.py:42: maybe stale\nwhat do you make of it"
    own = tr.user_own_words(prompt)
    assert "I think" not in own and "maybe" not in own


def test_a_marker_past_the_framing_window_does_not_count(tr):
    """A hypothesis being handed over arrives at the top, not on line 400."""
    prompt = "please review this transcript\n" + ("x" * 5000) + f"\n{HYPOTHESIS}"
    assert tr.build_investigation_proportionality_check(prompt) is None


def test_the_users_own_marker_still_arms_it(tr):
    """POSITIVE CONTROL — narrowing must not silence the case with teeth.

    Without this the tests above pass against a normaliser that returns "".
    """
    assert tr.build_investigation_proportionality_check(HYPOTHESIS) is not None
    assert tr.build_investigation_proportionality_check("quick check on the resolver please") is not None


def test_the_marker_survives_when_the_paste_comes_after_it(tr):
    """The common real shape: the user frames, then pastes the evidence."""
    prompt = f"{HYPOTHESIS} — here's the output:\n```\n" + ("log line\n" * 500) + "```"
    assert tr.build_investigation_proportionality_check(prompt) is not None


# ── the semantic pointer ─────────────────────────────────────────────────────


def test_the_pointer_covers_what_the_pattern_list_cannot(tr):
    """A bare assertion of fact IS handing over a hypothesis, and matches nothing.

    Taken verbatim from the corpus. This is the canonical case the block exists
    for and the case its own patterns miss.
    """
    prompt = "They do exist /home/yogapad/empirical-ai/empirica-outreach/.empirica/sessions/sessions.db"
    assert tr.build_investigation_proportionality_check(prompt) is None, "the list misses it — that is the point"
    assert tr.build_proportionality_pointer(prompt) is not None


def test_the_pointer_names_no_keywords(tr):
    """It must delegate the judgement, not carry a second vocabulary to complete."""
    text = tr.PROPORTIONALITY_POINTER
    assert "in any words" in text
    for word in ("I think", "maybe", "probably", "quick check"):
        assert word not in text


def test_block_and_pointer_are_mutually_exclusive(tr):
    """Both firing would double the ask and halve the attention it gets."""
    for prompt in (HYPOTHESIS, "They do exist /some/path/here.db", "refactor the resolver to use the registry"):
        assert not (tr.build_investigation_proportionality_check(prompt) and tr.build_proportionality_pointer(prompt))


@pytest.mark.parametrize("prompt", ["ok", "yes", "/release", "go on"])
def test_neither_fires_on_trivial_or_slash_input(tr, prompt):
    assert tr.build_investigation_proportionality_check(prompt) is None
    assert tr.build_proportionality_pointer(prompt) is None


# ── hedges ───────────────────────────────────────────────────────────────────


def test_hedge_detection_reads_the_users_words_not_the_paste(tr):
    """Telling someone not to mirror hedging they only QUOTED is noise.

    Asserted through `_build_aap_context`, the consumer, rather than through
    `detect_hedges` alone — the normalisation has to be applied at the call site
    to have any effect, and a test on the pure function would pass either way.
    """
    pasted = "review this:\n```\nwe might possibly want to perhaps consider it\n```"
    assert tr.detect_hedges(pasted), "the raw text does hedge — the instrument is live"
    assert tr._build_aap_context(pasted) == "", "but it is not the user's own hedging"


def test_the_users_own_hedging_still_surfaces(tr, monkeypatch):
    """POSITIVE CONTROL for the same call site — AAP force-enabled.

    `_build_aap_context` returns "" when AAP is off in config, so accepting an
    empty result here would let this control pass on any box where the protocol
    happens to be disabled: a green assertion proving nothing, which is the shape
    the suppressed test above exists to distinguish from. Pin the config instead.
    """
    monkeypatch.setattr(tr, "load_aap_config", lambda: {"enabled": True})
    own = "we might possibly want to perhaps consider maybe doing it that way"
    ctx = tr._build_aap_context(own)
    assert "aap-hedge-detected" in ctx

    # And the suppression above is NOT the config being off — same pinned config.
    pasted = "review this:\n```\nwe might possibly want to perhaps consider it\n```"
    assert tr._build_aap_context(pasted) == ""

"""wake_instance sends Space + Enter into a pane; it must only do that to a pane that is waiting for input (goal 87772ac4).

Space+Enter into a pane that is not idle does harm: on a permission dialog Enter accepts the default option, and on a half-typed
draft it submits the user's partial text with a space on the end. The wake is also about to be driven by a timer and by events, so
nobody is watching when it fires. The guard therefore fails closed: only a pane that positively looks idle is written to, and a
pane that cannot be read is not written to either.

The screen fixtures have the shape of real Claude Code captures taken from a busy and from idle panes on 2026-10-23: the spinner
line `<glyph> <Verb>… (12s · ↓ 825 tokens`, the empty `❯` prompt between two rules, the status lines below. The dialog is the
known Claude Code permission-prompt shape and was NOT captured here (every pane on that box ran in bypass-permissions mode), so
that case is held by construction: its selected option is refused by name, and a dialog has no empty prompt box, which the idle
test requires.
"""

from __future__ import annotations

import pytest

from empirica.core.cockpit import instance_actions as ia

RULE = "─" * 96
STATUS = "   myproject  │ TEST 85% │ G51 U91 │ 24%ctx\n  ⏵⏵ bypass permissions on · 2 shells · ← for agents"


def _screen(*above: str, prompt: str = "❯ ") -> str:
    return "\n".join([*above, RULE, prompt, RULE, STATUS])


IDLE = _screen("● Done. Two files changed.", "")
BUSY = _screen("● Bash(pytest -q)", "  ⎿  Running…", "", "✶ Zesting… (12s · ↓ 825 tokens · thought for 4s)", "")
BUSY_LONG = _screen("", "✻ Cogitating… (1m 5s · ↓ 4.1k tokens)", "")
DRAFT = _screen("● Done.", "", prompt="❯ half a sentence the user is typing")
WRAPPED_DRAFT = "\n".join(
    [
        "● Done.",
        "",
        RULE,
        "❯ a long draft that the terminal wrapped because it ran past the edge of the",
        "  box and carries on here.",
        RULE,
        STATUS,
    ]
)
DIALOG = "\n".join(
    [
        " Bash command",
        "   rm -rf build/",
        " Do you want to proceed?",
        " ❯ 1. Yes",
        "   2. Yes, and don't ask again for rm commands",
        "   3. No, and tell Claude what to do differently",
    ]
)
NUMBERED_ANSWER_ABOVE = _screen("● The options are:", "  1. keep it", "  2. drop it", "")


@pytest.mark.parametrize(
    ("name", "screen"),
    [("idle", IDLE), ("idle after a numbered list", NUMBERED_ANSWER_ABOVE)],
)
def test_a_pane_waiting_with_an_empty_prompt_is_idle(name, screen):
    idle, reason = ia.pane_is_idle(screen)
    assert idle, f"{name}: {reason}"


@pytest.mark.parametrize(
    ("name", "screen", "why"),
    [
        ("busy", BUSY, "turn is running"),
        ("busy past a minute", BUSY_LONG, "turn is running"),
        ("a half-typed draft", DRAFT, "at the prompt"),
        ("a draft wrapped onto a second line", WRAPPED_DRAFT, "at the prompt"),
        ("a permission dialog", DIALOG, "dialog"),
        ("an empty capture", "", "empty"),
        ("only blank lines", "\n\n  \n", "empty"),
        ("no input box on screen", "● some output\nand more output", "prompt"),
    ],
)
def test_a_pane_that_is_not_waiting_is_refused_with_a_reason(name, screen, why):
    idle, reason = ia.pane_is_idle(screen)
    assert not idle, name
    assert why in reason, f"{name}: reason was {reason!r}"


def test_the_busy_fixture_has_an_empty_prompt_too():
    """Control: the busy screen differs from the idle one only by the spinner, so it is the spinner check that refuses it."""
    assert BUSY.splitlines()[-4].strip() == "❯"
    assert ia.pane_is_idle(
        BUSY.replace("✶ Zesting… (12s · ↓ 825 tokens · thought for 4s)", "").replace("  ⎿  Running…", "")
    )[0]


# ── wake_instance end to end, with a fake tmux that answers capture-pane and records send-keys ──


class _Tmux:
    """Answers `tmux capture-pane` with a screen and records every `tmux send-keys`."""

    def __init__(self, screen: str | None, capture_rc: int = 0):
        self.screen, self.capture_rc, self.sent, self.calls = screen, capture_rc, [], []

    def run(self, args, **kw):
        self.calls.append(args)

        class _R:
            returncode, stdout, stderr = 0, "", ""

        r = _R()
        if args[1] == "capture-pane":
            r.returncode, r.stdout = self.capture_rc, self.screen or ""
            r.stderr = "" if self.capture_rc == 0 else "can't find pane"
        elif args[1] == "send-keys":
            self.sent.append(args)
        return r


@pytest.fixture
def tmux(monkeypatch):
    def install(screen, capture_rc=0):
        fake = _Tmux(screen, capture_rc)
        monkeypatch.setattr(ia.shutil, "which", lambda b: "/usr/bin/tmux" if b == "tmux" else None)
        monkeypatch.setattr(ia.subprocess, "run", fake.run)
        return fake

    return install


def test_an_idle_pane_is_woken(tmux):
    fake = tmux(IDLE)
    result = ia.wake_instance("tmux_5")
    assert result.success is True and result.method == "tmux-send-keys"
    assert len(fake.sent) == 1 and fake.sent[0][3] == "%5"


@pytest.mark.parametrize("screen", [BUSY, DRAFT, WRAPPED_DRAFT, DIALOG])
def test_a_pane_that_is_not_idle_receives_no_keys(tmux, screen):
    fake = tmux(screen)
    result = ia.wake_instance("tmux_5")
    assert result.success is False and result.method == "refused"
    assert fake.sent == [], "nothing may be typed into a pane that is not waiting for input"
    assert "not woken" in result.detail


def test_a_pane_that_cannot_be_read_receives_no_keys(tmux):
    fake = tmux(None, capture_rc=1)
    result = ia.wake_instance("tmux_5")
    assert result.success is False and result.method == "refused"
    assert fake.sent == []
    assert "cannot be read" in result.detail


def test_require_idle_false_keeps_the_old_unconditional_wake(tmux):
    fake = tmux(BUSY)
    assert ia.wake_instance("tmux_5", require_idle=False).success is True
    assert len(fake.sent) == 1


# ── a dim suggestion is not a draft (goal 87772ac4, live trial 2026-10-24) ──
#
# The first live trial woke nothing: the event reached the listener and the guard refused this seat's own idle pane because "something
# is at the prompt". Claude Code shows a greyed suggestion there after a turn, so the commonest idle state looked like a draft.
# `capture-pane -e` keeps the styling: a suggestion is wrapped in dim (SGR 2), typed text is not. The guard now tells them apart in
# its reason, and a wake may be allowed over a suggestion only when asked (allow_suggestion), because what Space+Enter does to a
# suggestion is being measured, not assumed.

DIM, OFF = "\x1b[2m", "\x1b[0m"
SUGGESTION = _screen("● Done.", "", prompt=f"❯ {DIM}run the failing test again{OFF}")
SUGGESTION_WRAPPED = "\n".join(
    [
        "● Done.",
        "",
        RULE,
        f"❯ {DIM}a longer suggestion that wraps onto{OFF}",
        f"  {DIM}a second line{OFF}",
        RULE,
        STATUS,
    ]
)
DRAFT_AFTER_DIM = _screen("● Done.", "", prompt=f"❯ {DIM}suggestion{OFF} and then typed text")
STYLED_DRAFT = _screen("● Done.", "", prompt="❯ \x1b[39mplain typed text\x1b[0m")


def test_a_suggestion_is_named_as_one_and_still_refused_by_default():
    idle, reason = ia.pane_is_idle(SUGGESTION)
    assert not idle and "suggestion" in reason


@pytest.mark.parametrize(
    ("name", "screen"),
    [("a typed draft", DRAFT), ("a styled typed draft", STYLED_DRAFT), ("dim then typed", DRAFT_AFTER_DIM)],
)
def test_typed_text_is_named_as_a_draft_never_as_a_suggestion(name, screen):
    idle, reason = ia.pane_is_idle(screen)
    assert not idle and "draft" in reason and "suggestion" not in reason, name


def test_a_wrapped_suggestion_is_a_suggestion_only_when_every_line_is_dim():
    assert "suggestion" in ia.pane_is_idle(SUGGESTION_WRAPPED)[1]
    half_dim = SUGGESTION_WRAPPED.replace(f"  {DIM}a second line{OFF}", "  typed second line")
    assert "draft" in ia.pane_is_idle(half_dim)[1]


def test_allow_suggestion_lets_pane_is_idle_pass_a_pure_suggestion_and_nothing_else():
    assert ia.pane_is_idle(SUGGESTION, allow_suggestion=True)[0] is True
    assert ia.pane_is_idle(SUGGESTION_WRAPPED, allow_suggestion=True)[0] is True
    for screen in (DRAFT, STYLED_DRAFT, DRAFT_AFTER_DIM, BUSY, DIALOG):
        assert ia.pane_is_idle(screen, allow_suggestion=True)[0] is False


def test_escape_codes_do_not_change_the_other_verdicts():
    assert ia.pane_is_idle(IDLE.replace("● Done.", "\x1b[32m● Done.\x1b[0m"))[0] is True
    assert ia.pane_is_idle(BUSY.replace("Zesting", "\x1b[33mZesting\x1b[0m"))[0] is False


def test_wake_instance_wakes_over_a_suggestion_only_when_allowed(tmux):
    fake = tmux(SUGGESTION)
    refused = ia.wake_instance("tmux_5")
    assert refused.method == "refused" and "suggestion" in refused.detail and fake.sent == []
    allowed = ia.wake_instance("tmux_5", allow_suggestion=True)
    assert allowed.success is True and len(fake.sent) == 1


def test_wake_instance_reads_the_pane_with_its_styling(tmux):
    fake = tmux(IDLE)
    ia.wake_instance("tmux_5")
    capture = next(a for a in fake.calls if a[1] == "capture-pane")
    assert "-e" in capture, "without -e the dim styling that identifies a suggestion is gone"

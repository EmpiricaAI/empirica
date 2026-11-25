"""Deprecated flags get the same notice as deprecated commands (goal 95c3ee16).

The list in config/deprecated_commands.yaml held commands only, so aliases kept as flags (postflight-submit --changes and the
like) had no way to announce their removal.
"""

from __future__ import annotations

from empirica.core import deprecations


def test_every_flag_entry_is_complete():
    flags = deprecations.deprecated_flags()
    assert flags, "the four 1.18.0 flags are listed"
    for e in flags:
        assert (
            e.get("command") and str(e.get("flag", "")).startswith("--") and e.get("remove_in") and e.get("reason")
        ), e


def test_a_used_flag_prints_its_notice():
    lines = deprecations.flag_notices("postflight-submit", ["postflight-submit", "-", "--changes", "x"])
    assert len(lines) == 1 and "--changes" in lines[0] and "--reasoning" in lines[0] and "1.18.0" in lines[0]


def test_the_equals_form_counts_and_other_commands_do_not():
    assert deprecations.flag_notices("checkpoint-load", ["checkpoint-load", "--format=json"])
    assert deprecations.flag_notices("preflight-submit", ["preflight-submit", "--changes", "x"]) == []
    assert deprecations.flag_notices("postflight-submit", ["postflight-submit", "--reasoning", "x"]) == []


def test_every_listed_flag_exists_on_its_command():
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    for e in deprecations.deprecated_flags():
        options = {o for a in sub.choices[e["command"]]._actions for o in a.option_strings}
        assert e["flag"] in options, e


def test_no_deprecated_flag_outlives_its_removal_release():
    from empirica import __version__

    def release(v):
        return tuple(int(x) for x in str(v).split(".")[:3])

    overdue = [e for e in deprecations.deprecated_flags() if release(__version__) >= release(e["remove_in"])]
    assert not overdue, f"remove these flags (and their entries) in {__version__}: {overdue}"

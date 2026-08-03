"""`--cite` must be reachable on every artifact type, not just findings.

`_resolve_source_ids` in the artifact-log handlers already read `cite_title` /
`cite_url` / `cite_type` for EVERY type. Only `finding-log` exposed the flags,
so on the other five the handler support existed and was unreachable from the
CLI — capability present, affordance absent.

That is the mirror of the defect this codebase keeps producing: usually an
advertised flag that does nothing, here a working feature nobody could invoke.
Both are gaps between what the interface offers and what the code does.
"""

from __future__ import annotations

import argparse

import pytest

from empirica.cli.cli_core import create_argument_parser

_ARTIFACT_VERBS = (
    "finding-log",
    "unknown-log",
    "deadend-log",
    "mistake-log",
    "decision-log",
    "assumption-log",
)


def _subparsers() -> dict:
    parser = create_argument_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices
    raise AssertionError("no subparsers found")


def _long_opts(sub) -> set[str]:
    return {o for a in sub._actions for o in getattr(a, "option_strings", ()) if o.startswith("--")}


@pytest.mark.parametrize("verb", _ARTIFACT_VERBS)
def test_every_artifact_verb_accepts_cite(verb):
    opts = _long_opts(_subparsers()[verb])

    missing = {"--cite", "--cite-url", "--cite-type"} - opts
    assert not missing, f"{verb} cannot cite inline; missing {sorted(missing)}"


@pytest.mark.parametrize("verb", _ARTIFACT_VERBS)
def test_every_artifact_verb_accepts_source(verb):
    assert "--source" in _long_opts(_subparsers()[verb])


@pytest.mark.parametrize("verb", _ARTIFACT_VERBS)
def test_cite_dests_match_what_the_handler_reads(verb):
    """The flags must land on the attribute names `_resolve_source_ids` reads.

    A correctly-named flag writing to the wrong dest would be an advertised
    no-op — accepted, discarded, no error. That is the exact shape this repo
    has produced three times this week, so it is asserted rather than assumed.
    """
    sub = _subparsers()[verb]
    dests = {a.dest for a in sub._actions}

    assert {"cite_title", "cite_url", "cite_type", "source_ids"} <= dests


def test_the_flags_come_from_one_definition():
    """Six hand-maintained copies would drift; one helper cannot."""
    import inspect

    from empirica.cli.parsers import checkpoint_parsers

    src = inspect.getsource(checkpoint_parsers)
    assert src.count('"--cite",') == 1, "the --cite flag must be defined once, in _add_cite_flags"
    assert src.count("_add_cite_flags(") >= 7, "helper defined once and applied to all six verbs"

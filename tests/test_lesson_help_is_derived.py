"""A schema learned by bisection is a schema the CLI never taught.

`lesson-create`'s flags describe HOW to pass JSON and say nothing about what may be
in it, so a first-time author discovers the payload shape by submitting wrong
payloads until one is accepted. Measured on a peer seat: five sequential failures,
each error correct and legible, each surfacing exactly one field.

The accepted-field list already existed — the unknown-field rejection enumerates all
fifteen. It was only ever reachable by failing first. The help text now carries it,
DERIVED from the same constants the validator enforces, because a hand-written copy
is a second source of truth for a schema that already has one.
"""

from __future__ import annotations

import json
import re
from argparse import Namespace

import pytest

from empirica.cli.cli_core import create_argument_parser
from empirica.cli.command_handlers.lesson_commands import (
    KNOWN_LESSON_KEYS,
    LESSON_ENUMS,
    handle_lesson_create_command,
)


@pytest.fixture(scope="module")
def epilog() -> str:
    parser = create_argument_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and a.choices and "lesson-create" in a.choices)
    return sub.choices["lesson-create"].epilog or ""


def test_every_accepted_field_is_named(epilog):
    """THE point. Add a field to the validator and the help gains it for free; replace
    the derivation with a hand-list and the next added field breaks this test."""
    missing = sorted(k for k in KNOWN_LESSON_KEYS if k not in epilog)
    assert not missing, f"accepted fields absent from --help: {missing}"


def test_every_closed_vocabulary_value_is_named(epilog):
    """An enum whose values are rejected but never listed is discoverable only by
    guessing. `cross_domain` reads as the obvious value for a cross-practice pattern
    and is not one."""
    for field, allowed in LESSON_ENUMS.items():
        assert field in epilog
        for value in allowed:
            assert value in epilog, f"{field} value {value!r} not documented"


def test_the_divergence_from_visibility_is_explained_not_just_listed(epilog):
    """Naming `sharing_policy` without saying why it is not `visibility` leaves the
    reader assuming drift and reaching for `shared`, which is rejected."""
    assert "visibility" in epilog
    assert "shared~org" in epilog


def test_the_worked_example_actually_works(tmp_path, monkeypatch):
    """RUN what the docs tell someone else to run.

    An example is an instruction, and an instruction nobody executed is a claim. This
    practice has shipped a help string citing a verb that does not exist; the
    prevention is mechanical, not intentional — so the example is parsed out of the
    help text and executed rather than eyeballed.
    """
    monkeypatch.chdir(tmp_path)
    parser = create_argument_parser()
    sub = next(a for a in parser._actions if hasattr(a, "choices") and a.choices and "lesson-create" in a.choices)
    epilog = sub.choices["lesson-create"].epilog or ""

    m = re.search(r"--json '(\{.*\})'", epilog, re.DOTALL)
    assert m, "no runnable --json example in the help text"
    payload = json.loads(m.group(1))  # fails loudly if the example is not valid JSON

    result = handle_lesson_create_command(Namespace(json=json.dumps(payload), input=None, name=None, output="json"))

    assert result["ok"] is True, result.get("error")
    assert result["step_count"] == 1, "the example's step did not survive — it teaches a shape that stores nothing"

"""Shared Epistemic Records are retired from core (cortex stage 2, 2026-10-24; David confirmed first-hand: remove outright, keep the tables).

Cortex closed all 104 SERs, switched escalation and auto-block off, and now refuses `cortex_propose(payload.action='create_ser')`; the
replacement for "is every target done" is one multi-target proposal with a per-target ack (97820077). Core still carried the consumer
side: a `mailbox sers` verb, a POSTFLIGHT check that asked cortex about SER ack debt (an HTTP call on every POSTFLIGHT), a catch-up
step that fetched `/v1/sers` on every listener poll to recover dropped escalations, a listener relay for `ser_escalation` wakes, and
docstrings that told callers to use `create_ser`, which would now fail.

These tests keep the retirement retired. They scan the shipped package for the retired surface by token, so a half-removed consumer or a
new caller of the dead endpoint fails here. Comments that cite an old SER by id as design history are not retired surface and are not
matched; the tables stay in the schema.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "empirica"
TEXT_SUFFIXES = {".py", ".md", ".json", ".yaml", ".yml", ".sh", ".txt", ".toml"}

# Tokens that only exist because of the retired surface. Each is specific enough not to match `create_server` or `user_id`.
RETIRED_TOKENS = (
    "create_ser'",  # the cortex action name as a string literal
    'create_ser"',
    "create_ser`",
    "/v1/sers",
    "cortex_ser_ack",
    "transition_ser",
    "ser_escalation",
    "ACTIVE_SER_STATES",
    "reconcile_ser_escalations",
    "_maybe_add_ser_ack_debt_note",
    "ser_ack_check",
    "ser_acks_owed",
)


def _shipped_files(root: Path = PACKAGE):
    for path in root.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts:
            yield path


def _offenders(token: str, root: Path = PACKAGE) -> list[str]:
    hits = []
    for path in _shipped_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if token in text:
            hits.append(path.name)
    return hits


@pytest.mark.parametrize("token", RETIRED_TOKENS)
def test_the_retired_surface_is_gone_from_the_shipped_package(token):
    assert _offenders(token) == []


def test_the_scan_finds_a_planted_hit_and_walks_the_real_package(tmp_path):
    """Positive control: a scan that matches nothing anywhere would also pass. It finds a planted token, and it does walk the package."""
    (tmp_path / "x.py").write_text("url = '/v1/sers'\n")
    (tmp_path / "clean.md").write_text("nothing here\n")
    assert _offenders("/v1/sers", tmp_path) == ["x.py"]
    assert sum(1 for _ in _shipped_files()) > 500, "the real package scan must see hundreds of files"


def test_mailbox_has_no_sers_verb():
    out = subprocess.run(
        [sys.executable, "-m", "empirica.cli", "mailbox", "sers"], capture_output=True, text=True, timeout=60, cwd=ROOT
    )
    assert out.returncode != 0
    assert "invalid choice" in out.stderr, out.stderr[-300:]


def test_mailbox_help_does_not_offer_sers():
    out = subprocess.run(
        [sys.executable, "-m", "empirica.cli", "mailbox", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ROOT,
    )
    assert out.returncode == 0 and "reply" in out.stdout, "control: the help rendered and lists the live verbs"
    assert "sers" not in out.stdout


def test_the_listener_relays_only_the_wake_shapes_that_still_exist():
    from empirica.core.loop_scheduler import listener

    assert frozenset({"platform_dispatch_ready"}) == listener._NON_PROPOSAL_WAKE_SHAPES


def test_the_content_poll_has_no_ser_event_or_fetch():
    from empirica.core.loop_scheduler import content_poll

    for name in ("EscalationEvent", "reconcile_ser_escalations", "_fetch_sers", "ACTIVE_SER_STATES"):
        assert not hasattr(content_poll, name), name
    assert hasattr(content_poll, "ProposalEvent"), "control: the live event type is still there"


def test_no_doc_or_prompt_still_tells_a_reader_to_run_the_removed_verb():
    """The Sentinel reference listed `mailbox poll|show|sers` as read-only after the verb was gone; the package scan above does not
    reach docs/. A reader (or a model) following that line is told about a verb that fails with `invalid choice`."""
    hits = [
        str(p.relative_to(ROOT))
        for p in _shipped_files(ROOT / "docs")
        if "mailbox sers" in p.read_text(encoding="utf-8")
    ]
    hits += [
        str(p.relative_to(ROOT)) for p in _shipped_files(PACKAGE) if "mailbox sers" in p.read_text(encoding="utf-8")
    ]
    assert hits == []
    assert sum(1 for _ in _shipped_files(ROOT / "docs")) > 50, "control: the docs scan walked the docs"

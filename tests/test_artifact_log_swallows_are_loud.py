"""Two requested effects that used to fail at DEBUG now fail at WARNING.

- inline `--cite`: the practitioner asked for a citation; if create_source
  raises, the artifact ships unsourced and the POSTFLIGHT "0 source_refs" nag
  reads as their omission.
- goal auto-attach: a tier query RAISING is not a tier finding nothing; the
  artifact ships unattached either way, but only one is a defect.

The CLI installs no logging handler, so WARNING reaches stderr through
Python's lastResort handler and DEBUG never does — verified by a probe on
this box before the levels were chosen.
"""

from __future__ import annotations

import logging
import types

from empirica.cli.command_handlers import artifact_log_commands as alc


def test_a_failed_inline_cite_warns_and_still_returns_the_other_sources(caplog):
    class _BC:
        def create_source(self, **kw):
            raise RuntimeError("sources table locked")

    db = types.SimpleNamespace(breadcrumbs=_BC())
    args = types.SimpleNamespace(source_ids=["src-existing"], cite_title="A paper", cite_url=None, cite_type=None)
    with caplog.at_level(logging.WARNING, logger=alc.logger.name):
        ids = alc._resolve_source_ids(args, {"project_id": "p", "session_id": "s"}, db)
    assert ids == ["src-existing"]
    assert "source was NOT created" in caplog.text
    assert "sources table locked" in caplog.text


def test_a_raising_attach_tier_warns_instead_of_reading_as_no_goal(caplog):
    class _Cur:
        def execute(self, *a, **k):
            raise RuntimeError("no such column: transaction_id")

        def fetchone(self):
            return None

    class _Conn:
        def cursor(self):
            return _Cur()

    db = types.SimpleNamespace(conn=_Conn())
    with caplog.at_level(logging.WARNING, logger=alc.logger.name):
        out = alc._resolve_goal_for_artifact(None, "sess", db, transaction_id="tx", project_id="p")
    assert out is None
    assert "tier query FAILED" in caplog.text
    assert "no such column" in caplog.text

"""docs-assess --check-staleness exits non-zero when the check itself reports ok: False.

Found by the 2026-10-06 pipeline sweep (U1): the handler printed {"ok": false, "error": "No docs directory found"} and returned 0.
"""

from __future__ import annotations

import json
import types

import pytest

from empirica.cli.command_handlers import docs_commands as dc


class _StubAgent:
    def __init__(self, result):
        self._result = result

    def check_staleness(self, threshold, lookback_days):
        return self._result


def _args(**kw):
    return types.SimpleNamespace(
        project_root=None,
        verbose=False,
        output="json",
        summary_only=False,
        check_docstrings=False,
        turtle=False,
        check_staleness=True,
        staleness_threshold=0.7,
        staleness_days=30,
        **kw,
    )


@pytest.mark.parametrize(
    ("result", "code"),
    [
        ({"ok": False, "error": "No docs directory found"}, 1),
        ({"ok": True, "stale_docs": []}, 0),
        ({"stale_docs": []}, 0),  # a result with no ok key is not a failure
    ],
)
def test_staleness_exit_code_follows_the_result(monkeypatch, capsys, result, code):
    monkeypatch.setattr(dc, "EpistemicDocsAgent", lambda **kw: _StubAgent(result))
    assert dc.handle_docs_assess(_args()) == code
    assert json.loads(capsys.readouterr().out) == result  # the output is unchanged: only the exit code differs


def test_the_human_output_path_has_the_same_exit_code(monkeypatch, capsys):
    monkeypatch.setattr(
        dc, "EpistemicDocsAgent", lambda **kw: _StubAgent({"ok": False, "error": "No docs directory found"})
    )
    monkeypatch.setattr(dc, "_print_staleness_output", lambda result, verbose: print("printed"))
    args = _args()
    args.output = "human"
    assert dc.handle_docs_assess(args) == 1
    assert "printed" in capsys.readouterr().out

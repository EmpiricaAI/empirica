"""Dispatch resilience — a feature verb's broken registration must NOT brick
the life-support verbs (preflight/check/postflight/recovery).

Regression guard for the P1 incident (prop_m2orjtozsvco5ansfqdxi2g5fu): a single
feature verb whose handler failed to import raised NameError while building the
dispatch table, which took down EVERY verb including preflight — and because
preflight is the verb the praxic firewall needs, it fail-closed into an
unrecoverable deadlock (can't preflight to fix what broke preflight).

The fix pre-seeds the dispatch with a life-support set and, on a table-build
NameError, dispatches from it — so the loop-opening + recovery verbs always work.
"""

from __future__ import annotations

import pytest

from empirica.cli import cli_core


def test_life_support_handlers_has_core_verbs():
    ls = cli_core._life_support_handlers()
    # The epistemic loop's spine + recovery escape must all resolve to callables.
    for verb in (
        "preflight-submit",
        "check-submit",
        "postflight-submit",
        "session-create",
        "doctor",
        "diagnose",
    ):
        assert verb in ls, f"life-support missing {verb}"
        assert callable(ls[verb])


def test_dispatch_survives_feature_verb_nameerror(monkeypatch):
    """Break a feature verb's handler, then confirm a life-support verb still
    dispatches (rather than the whole CLI dying with NameError)."""
    called = {}

    def stub_doctor(args):
        called["doctor"] = True
        return 0

    # Route the life-support 'doctor' to a recording stub...
    monkeypatch.setattr(cli_core, "handle_doctor_command", stub_doctor)
    # ...and BREAK a feature verb so the full dispatch-table literal NameErrors
    # while it's being built (the exact failure mode of the incident).
    monkeypatch.delattr(cli_core, "handle_sources_check_command", raising=False)

    with pytest.raises(SystemExit) as exc:
        cli_core.main(["doctor"])

    assert called.get("doctor") is True, "life-support verb did not dispatch after a table-build NameError"
    assert exc.value.code == 0  # stub returned 0 → clean exit, no deadlock


def test_unknown_verb_after_nameerror_reports_unavailable(monkeypatch, capsys):
    """A NON-life-support verb, when the table failed to build, reports itself
    unavailable (loudly) — it does not silently succeed or hang."""
    monkeypatch.delattr(cli_core, "handle_sources_check_command", raising=False)

    with pytest.raises(SystemExit) as exc:
        cli_core.main(["goals-list"])  # a feature verb, not in life-support

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "unavailable" in err and "registration error" in err

"""The firewall must not be able to wedge with its own remedy denied.

Two independent halves of one incident, observed live:

1. A PREFLIGHT returned ``ok: true`` with a transaction_id and wrote NO
   ``active_transaction*.json`` — the Sentinel's only input. The write sat behind
   ``except Exception -> logger.debug("...non-fatal...")``. It is not non-fatal.
   The transaction existed only in ``sessions.db-wal``, the gate stayed pinned to
   a stale closed transaction, and every praxic call was denied with *Epistemic
   loop closed. Run new PREFLIGHT* — blaming the practitioner for not doing the
   thing they had just done.

2. Recovering from that state meant running the command the deny names. But the
   tier lists are literal prefixes matched with ``startswith``, so::

       empirica --verbose preflight-submit -   -> DENIED
       empirica preflight-submit -             -> ALLOWED   (identical payload)

   ``--verbose`` is exactly what someone adds when a command is misbehaving, so
   the gate selected against the person debugging it.

Broccoli: *unfalsifiable success* (1) feeding *gate-gates-its-own-escape* (2).
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

HOOK = Path(__file__).parent.parent / "empirica" / "plugins" / "claude-code-integration" / "hooks" / "sentinel-gate.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("sentinel_gate_under_test", HOOK)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    return _load_hook()


# ── half 2: the allow-list must parse past global flags ──────────────────────


def test_the_denys_own_remedy_is_allowed_with_verbose(gate):
    """THE regression. This exact command was denied while the gate was wedged,
    and it is the one the deny message tells you to run."""
    assert gate.is_safe_empirica_command("empirica --verbose preflight-submit -")


def test_the_unadorned_form_still_works(gate):
    """POSITIVE CONTROL. If this ever fails the normalizer broke the common path,
    which is worse than the bug it fixes."""
    assert gate.is_safe_empirica_command("empirica preflight-submit -")


@pytest.mark.parametrize(
    "cmd",
    [
        "empirica -v goals-list",
        "empirica --verbose finding-log --finding x",
        "empirica --verbose -v postflight-submit -",
    ],
)
def test_every_global_flag_spelling_is_transparent(gate, cmd):
    assert gate.is_safe_empirica_command(cmd)


def test_an_unknown_flag_is_NOT_stripped(gate):
    """NEGATIVE CONTROL, and the way this fix could have gone wrong.

    Stripping anything that looks like a flag would let an unrecognized option
    shape ride through the tier match. Only the three real global flags are
    removed; `--wat` leaves the command unrecognized, as it should be.
    """
    assert not gate.is_safe_empirica_command("empirica --wat rm-everything")


def test_a_mutating_verb_is_still_denied_behind_a_global_flag(gate):
    """The normalizer must not become a bypass: hiding a non-whitelisted verb
    behind `--verbose` gets it classified as that verb, not waved through."""
    assert not gate.is_safe_empirica_command("empirica --verbose rebuild --qdrant")


def test_stripping_is_only_leading(gate):
    """`--verbose` AFTER the verb is an argument to the verb, not a global flag,
    and must not cause the verb itself to be re-parsed."""
    assert gate.is_safe_empirica_command("empirica goals-list --verbose")


def test_the_normalizer_preserves_a_command_it_does_not_touch(gate):
    """A command with no global flags must come back byte-identical — the fix
    must not reformat commands on its way past."""
    original = "empirica   finding-log   --finding 'a  b'"
    assert gate._strip_empirica_global_flags(original) == original


# ── half 1: a failed transaction-file write must be visible ──────────────────


@pytest.fixture
def preflight_mod():
    from empirica.cli.command_handlers import _workflow_preflight

    return _workflow_preflight


def test_visibility_check_finds_the_transaction_under_any_suffix(preflight_mod, tmp_path):
    """The Sentinel globs `active_transaction*.json` and ranks; it does not read
    one known filename. So a write that landed under an unexpected instance
    suffix DOES satisfy the firewall and must not be reported as a failure."""
    d = tmp_path / ".empirica"
    d.mkdir()
    (d / "active_transaction_tmux_41.json").write_text(json.dumps({"transaction_id": "abc-123"}))

    assert preflight_mod._transaction_file_visible(str(tmp_path), "abc-123")


def test_visibility_check_is_false_when_nothing_carries_the_id(preflight_mod, tmp_path):
    """THE regression's detector. Files existed — they just held a STALE
    transaction. Presence of a file is not presence of this transaction."""
    d = tmp_path / ".empirica"
    d.mkdir()
    (d / "active_transaction_tmux_6.json").write_text(json.dumps({"transaction_id": "some-older-tx"}))

    assert not preflight_mod._transaction_file_visible(str(tmp_path), "abc-123")


def test_visibility_check_survives_a_corrupt_sibling(preflight_mod, tmp_path):
    """One unreadable file must not hide a good one — the old code's habit of
    treating any exception as 'fine' is what this whole fix is about."""
    d = tmp_path / ".empirica"
    d.mkdir()
    (d / "active_transaction_a.json").write_text("{not json")
    (d / "active_transaction_b.json").write_text(json.dumps({"transaction_id": "abc-123"}))

    assert preflight_mod._transaction_file_visible(str(tmp_path), "abc-123")


def test_missing_directory_is_not_visible(preflight_mod, tmp_path):
    assert not preflight_mod._transaction_file_visible(str(tmp_path / "nope"), "abc-123")


def test_unresolvable_project_path_RAISES_instead_of_returning_none(preflight_mod, monkeypatch):
    """THE regression. It returned None behind a `logger.warning`, so the caller
    carried on and printed ok:true. A raise is what makes the caller unable to
    mistake it for an ordinary outcome."""
    monkeypatch.setattr(preflight_mod.R, "context", staticmethod(lambda: {}))
    monkeypatch.setattr(preflight_mod.R, "project_path", staticmethod(lambda _cs=None: None))

    with pytest.raises(preflight_mod.TransactionFileNotWritten):
        preflight_mod._preflight_write_transaction_file("sess", "tx", {})


def test_a_silent_write_noop_is_caught_by_the_readback(preflight_mod, monkeypatch, tmp_path):
    """The exact observed shape: `transaction_write` raises nothing and writes
    nothing. Trusting the producer's receipt is what let this ship — the check
    has to go through the surface the CONSUMER reads."""
    (tmp_path / ".empirica").mkdir()
    monkeypatch.setattr(
        preflight_mod.R, "context", staticmethod(lambda: {"claude_session_id": "cs", "project_path": str(tmp_path)})
    )
    monkeypatch.setattr(preflight_mod.R, "transaction_write", staticmethod(lambda **kw: None))
    monkeypatch.setattr(preflight_mod, "_preflight_promote_pending_calibration", lambda _p: None)

    with pytest.raises(preflight_mod.TransactionFileNotWritten) as exc:
        preflight_mod._preflight_write_transaction_file("sess", "tx-that-never-landed", {})

    assert "tx-that-never-landed" in str(exc.value)


def test_a_real_write_passes_the_readback(preflight_mod, monkeypatch, tmp_path):
    """POSITIVE CONTROL on the check above. A guard that always raises would be
    caught here rather than by every user of the CLI."""
    d = tmp_path / ".empirica"
    d.mkdir()

    def _write(**kw):
        (d / "active_transaction_test.json").write_text(json.dumps({"transaction_id": kw["transaction_id"]}))

    monkeypatch.setattr(
        preflight_mod.R, "context", staticmethod(lambda: {"claude_session_id": "cs", "project_path": str(tmp_path)})
    )
    monkeypatch.setattr(preflight_mod.R, "transaction_write", staticmethod(_write))
    monkeypatch.setattr(preflight_mod, "_preflight_promote_pending_calibration", lambda _p: None)
    monkeypatch.setattr(preflight_mod, "_preflight_enrich_transaction_file", lambda *a: None)
    monkeypatch.setattr(preflight_mod, "_preflight_inject_avg_turns", lambda *a: None)
    monkeypatch.setattr("empirica.utils.session_resolver.update_active_context", lambda **kw: None)

    assert preflight_mod._preflight_write_transaction_file("sess", "tx-ok", {}) == str(tmp_path)


def test_the_non_fatal_comment_is_gone_from_the_call_site():
    """The word was load-bearing in the wrong direction: it told every later
    reader the failure did not matter, and it is the firewall's only input.

    Asserted positively — what the call site must SAY — rather than by grepping
    for the absence of a phrase this file's own docstring quotes.
    """
    src = (
        Path(__file__).parent.parent / "empirica" / "cli" / "command_handlers" / "_workflow_preflight.py"
    ).read_text()

    idx = src.index("Stage 3b: Persist transaction file")
    block = src[idx : idx + 1600]

    assert "ONLY input the\n            # Sentinel firewall reads" in block
    assert "firewall_warning" in src, "the failure must ride out on the response, not only a log"

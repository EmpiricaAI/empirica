"""A lookup that never completed must not be reported as a lookup that found nothing.

`_default_fetch_parent` used to `except Exception: return None`, so both callers
printed:

    parent <id> not found or inaccessible. Check the id and your Cortex tenant scope.

for a timeout, a DNS failure, a 401, or a 500. That sentence is wrong twice: it
asserts an outcome the code never observed, and it names two innocent suspects.

Cortex's nginx logs carry the real shape — `499` (client closed the connection)
at 21:14:43 during a 3-5x request spike, then `200` for the same id 25 seconds
later. Two peer practices each spent an investigation hunting a lookup/visibility
defect that did not exist, because the message named one. A message that asserts
a cause its own code discarded turns every downstream investigation into a search
for something that isn't there.

These tests assert on the RENDERED stderr, not on the exception type: the message
is the artifact a practitioner acts on, and it was the message that lied.
"""

from __future__ import annotations

import contextlib
import io
import json
import types
import urllib.error

import pytest

from empirica.cli.command_handlers.mailbox_commands import (
    ParentFetchError,
    _default_fetch_parent,
    handle_mailbox_reply_command,
    handle_mailbox_show_command,
)

# ─── the two callers, driven through their injection seams ────────────


def _reply_args(**over):
    d = {
        "parent_id": "prop_parent",
        "summary": "s",
        "title": None,
        "type": "collab_brief",
        "target_claudes": None,
        "source_claude": "empirica",
        "payload": None,
        "result": "shipped",
        "commit_sha": None,
        "no_close": False,
        "no_archive": False,
        "output": "json",
    }
    d.update(over)
    return types.SimpleNamespace(**d)


def _show_args(**over):
    d = {"proposal_id": "prop_parent", "output": "json"}
    d.update(over)
    return types.SimpleNamespace(**d)


def _creds():
    return "https://cortex.example", "key123"


def _run(handler, args, fetch_parent):
    """Run a handler with its fetch seam replaced; return (rc, stderr)."""
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = handler(args, _resolve_cortex_creds=_creds, _fetch_parent=fetch_parent)
    return rc, err.getvalue()


def _raises(exc):
    def fn(*_args):
        raise exc

    return fn


def _returns_none(*_args):
    return None


CASES = [
    pytest.param(ParentFetchError("request timed out after 5.0s", retryable=True), "timed out", id="timeout"),
    pytest.param(
        ParentFetchError("could not reach cortex: [Errno -2] Name or service not known", retryable=True),
        "could not reach cortex",
        id="dns",
    ),
    pytest.param(
        ParentFetchError("cortex refused the request (HTTP 401) — credentials or tenant scope"),
        "HTTP 401",
        id="unauthorized",
    ),
    pytest.param(ParentFetchError("cortex returned HTTP 500", retryable=True), "HTTP 500", id="server-error"),
]


@pytest.mark.parametrize(("exc", "expected_fragment"), CASES)
@pytest.mark.parametrize(
    ("handler", "args_fn"),
    [(handle_mailbox_reply_command, _reply_args), (handle_mailbox_show_command, _show_args)],
    ids=["reply", "show"],
)
def test_a_failed_lookup_names_its_real_cause(handler, args_fn, exc, expected_fragment):
    rc, err = _run(handler, args_fn(), _raises(exc))

    assert rc == 1
    assert expected_fragment in err, f"the real cause is absent from: {err!r}"


@pytest.mark.parametrize(("exc", "_frag"), CASES)
@pytest.mark.parametrize(
    ("handler", "args_fn"),
    [(handle_mailbox_reply_command, _reply_args), (handle_mailbox_show_command, _show_args)],
    ids=["reply", "show"],
)
def test_a_failed_lookup_does_not_blame_the_id_or_the_tenant(handler, args_fn, exc, _frag):
    """The regression, stated as an assertion.

    This is the half that cost two practices an investigation each — not the
    missing cause, but the confidently-named wrong one.
    """
    _rc, err = _run(handler, args_fn(), _raises(exc))

    assert "not found" not in err, f"a lookup that never completed claimed absence: {err!r}"
    assert "tenant scope" not in err or "401" in err, f"an innocent suspect was named: {err!r}"


@pytest.mark.parametrize(
    ("handler", "args_fn"),
    [(handle_mailbox_reply_command, _reply_args), (handle_mailbox_show_command, _show_args)],
    ids=["reply", "show"],
)
def test_a_real_404_still_reports_absence(handler, args_fn):
    """The positive control.

    Without it, every assertion above would pass on a handler that had simply
    stopped saying "not found" at all — which would trade one wrong message for
    another and still read as green.
    """
    rc, err = _run(handler, args_fn(), _returns_none)

    assert rc == 1
    assert "not found" in err
    assert "404" in err, "an absence claim should say which observation supports it"


# ─── the discriminator itself, against a stubbed urlopen ──────────────


def _urlopen_raising(exc):
    def fake(*_a, **_kw):
        raise exc

    return fake


def test_404_returns_none_rather_than_raising(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _urlopen_raising(urllib.error.HTTPError("u", 404, "Not Found", {}, None)),  # type: ignore[arg-type]
    )

    assert _default_fetch_parent("https://c", "k", "prop_x") is None


@pytest.mark.parametrize(
    ("exc", "fragment", "retryable"),
    [
        (urllib.error.HTTPError("u", 500, "err", {}, None), "HTTP 500", True),  # type: ignore[arg-type]
        (urllib.error.HTTPError("u", 403, "no", {}, None), "HTTP 403", False),  # type: ignore[arg-type]
        (TimeoutError(), "timed out", True),
        (urllib.error.URLError(TimeoutError()), "timed out", True),
        (urllib.error.URLError("unreachable"), "could not reach cortex", True),
    ],
)
def test_everything_that_is_not_a_404_raises_with_its_cause(monkeypatch, exc, fragment, retryable):
    monkeypatch.setattr("urllib.request.urlopen", _urlopen_raising(exc))

    with pytest.raises(ParentFetchError) as caught:
        _default_fetch_parent("https://c", "k", "prop_x", timeout=5.0)

    assert fragment in caught.value.reason
    assert caught.value.retryable is retryable, "retryable drives whether the user is told to re-run"


def test_a_200_with_no_proposal_is_a_failure_not_an_absence(monkeypatch):
    """`200 {}` means the call worked and the contract did not hold.

    Returning None here would put a server-side contract break into the same
    bucket as a genuine 404 — the original defect, one layer in.
    """

    class _Resp:
        def read(self):
            return json.dumps({"unexpected": "shape"}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda _r, timeout=None: _Resp())

    with pytest.raises(ParentFetchError):
        _default_fetch_parent("https://c", "k", "prop_x")

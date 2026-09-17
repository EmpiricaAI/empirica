"""A failed resolution must not render as "not configured".

Both resolvers in `mailbox_commands` returned the same value for two different
states, and every caller answers that value with configuration advice:

  `(None, None)` from creds  → "Cortex creds missing — configure cortex.url + …"
  `None` from ai_id          → "set --ai-id or add ai_id to .empirica/project.yaml"

So an expired refresh token told the user to configure something already
configured, and a malformed `project.yaml` pointed them at a key that was already
present. The advice was not just wrong, it was unfollowable — nothing was missing.

Same shape as the timeout reported as "not found" that this file's
`_default_fetch_parent` carried until earlier today, one function below. Found by
classifying every BLE001 site in the repo rather than by looking here: 1,889 blind
excepts, 849 of them silent, 288 of those returning a VALUE indistinguishable from
a legitimate answer. These two were in the file I had already fixed once.

The return types are unchanged on purpose — `_resolve_cortex_creds` is injected
into every handler and every double returns a 2-tuple. The cause reaches the user
beside the caller's own message instead of through a new signature.
"""

from __future__ import annotations

import contextlib
import io

from empirica.cli.command_handlers.mailbox_commands import (
    _default_resolve_ai_id,
    _default_resolve_cortex_creds,
)


def _stderr_of(fn):
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        result = fn()
    return result, buf.getvalue()


def test_a_failed_credential_resolution_says_it_failed(monkeypatch):
    """The defect, as an assertion."""

    def _boom():
        raise RuntimeError("refresh token expired")

    monkeypatch.setattr("empirica.core.auth.cortex_bearer", _boom)

    result, err = _stderr_of(_default_resolve_cortex_creds)

    assert result == (None, None), "the tuple shape must not change — every test double returns one"
    assert "FAILED" in err
    assert "not the same as" in err, "it must contrast itself with the not-configured case"
    assert "refresh token expired" in err, "the real cause has to reach the user"


def test_a_successful_resolution_is_silent(monkeypatch):
    """Positive control.

    A note on every resolution would satisfy the test above while adding noise to
    every successful command — and a note that always fires is one nobody reads.
    """
    monkeypatch.setattr(
        "empirica.core.auth.cortex_bearer",
        lambda: {"url": "https://c", "bearer": "k"},
    )

    result, err = _stderr_of(_default_resolve_cortex_creds)

    assert result == ("https://c", "k")
    assert err == ""


def test_an_unparseable_project_yaml_is_distinguished_from_a_missing_one(tmp_path, monkeypatch):
    """A file that exists and cannot be parsed is not an absent key."""
    proj = tmp_path / ".empirica"
    proj.mkdir()
    (proj / "project.yaml").write_text("ai_id: [unclosed\n  bad: : :")
    monkeypatch.chdir(tmp_path)

    result, err = _stderr_of(_default_resolve_ai_id)

    assert result is None
    assert "unparseable" in err
    assert "may be present" in err, "the reader must be told the key might already be there"


def test_a_missing_project_yaml_is_silent(tmp_path, monkeypatch):
    """Positive control: genuinely absent is the not-configured case, and quiet."""
    monkeypatch.chdir(tmp_path)

    result, err = _stderr_of(_default_resolve_ai_id)

    assert result is None
    assert err == "", "absence is the state the caller message already describes correctly"


def test_a_readable_project_yaml_resolves_and_stays_silent(tmp_path, monkeypatch):
    proj = tmp_path / ".empirica"
    proj.mkdir()
    (proj / "project.yaml").write_text("ai_id: empirica\n")
    monkeypatch.chdir(tmp_path)

    result, err = _stderr_of(_default_resolve_ai_id)

    assert result == "empirica"
    assert err == ""

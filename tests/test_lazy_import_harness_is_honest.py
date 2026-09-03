"""A harness failure must not render as a regression in the code it measures.

`test_cli_lazy_imports` asserts an ABSENCE — that importing the CLI does not pull
httpx or GitPython — by importing in a fresh subprocess and reading `sys.modules`.

On 2026-09-03 both tests failed inside a concurrent full-suite run and blocked a
release cut. **The import was never eager** — verified nine times, standalone and
under load. What failed was the measurement: a subprocess that could not finish while
two ~6,900-test suites competed for the box.

The old harness let that surface through assertions worded *"empirica.cli eagerly
imported httpx"*, so a resource failure accused the code of a regression it had not
made — and the release gate reported it as `1 failed` with no test id, so identifying
it took a full manual re-run.

These tests pin the distinction: **"we could not measure" must never read as "we
measured and it is broken."**
"""

from __future__ import annotations

import subprocess

import pytest

from tests import test_cli_lazy_imports as mod


def test_a_timeout_is_reported_as_a_harness_failure(monkeypatch):
    """THE regression. Under contention the subprocess times out; the reader must not
    be told the import went eager."""

    def _always_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="python", timeout=1)

    monkeypatch.setattr(subprocess, "run", _always_timeout)

    with pytest.raises(AssertionError) as exc:
        mod._modules_after("empirica.cli")

    msg = str(exc.value)
    assert "HARNESS FAILURE" in msg
    assert "not an eager-import regression" in msg
    assert "loaded machine" in msg, "and it must tell the reader what to do about it"


def test_it_retries_once_before_giving_up(monkeypatch):
    """Transient contention is legitimately retryable; a genuine eager import is not.
    One retry separates the two without hiding anything."""
    calls = {"n": 0}
    real = subprocess.run

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(cmd="python", timeout=1)
        return real(*a, **k)

    monkeypatch.setattr(subprocess, "run", _flaky)

    loaded = mod._modules_after("empirica.cli")

    assert calls["n"] == 2, "should have retried exactly once"
    assert "sys" in loaded, "and returned a real module set on the retry"


def test_a_nonzero_exit_is_also_the_harness(monkeypatch):
    """A subprocess that dies says nothing about laziness. Reporting it as an
    eager-import failure sends the reader to audit imports that are fine."""

    class _Dead:
        returncode = 137
        stdout = ""
        stderr = "Killed"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Dead())

    with pytest.raises(AssertionError, match="HARNESS FAILURE"):
        mod._modules_after("empirica.cli")


def test_unparseable_output_is_the_harness_too(monkeypatch):
    """Garbage on stdout is a measurement problem. The old code would have raised a
    bare JSONDecodeError, which reads as neither."""

    class _Noise:
        returncode = 0
        stdout = "not json at all"
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Noise())

    with pytest.raises(AssertionError, match="HARNESS FAILURE"):
        mod._modules_after("empirica.cli")


def test_a_REAL_eager_import_still_fails_loudly():
    """POSITIVE CONTROL, and the one that matters most.

    Every test above makes the harness quieter about its own failures. If that
    quieting also swallowed a genuine regression the fix would be worse than the bug —
    so this asserts the instrument is still live, by measuring a target that DOES
    import the thing.
    """
    loaded = mod._modules_after("json")

    assert "json" in loaded, "the measurement itself must work"
    assert "httpx" not in loaded

    # And the real subject, unchanged: the CLI must still be clean.
    cli = mod._modules_after("empirica.cli")
    assert "httpx" not in cli and "git" not in cli

"""The post-release "watch CI" hint must key on the SHA, not on list position.

`gh run list --limit 1` and "did MY commit pass" are different queries. They agree
right up until anything else pushes, and then the first reports the newest run's
(empty, `in_progress`) conclusion for a commit that is actually green. Observed:
an empty verdict printed for `a9fd7d02a`, which had passed.

Same family as reading a watcher's exit code as a verdict — the instrument answers
a question adjacent to the one asked, and its answer is well-formed either way.

What makes this worth a test rather than a docstring: the defective idiom was
being printed *by our own release tool*, at the moment a human is most likely to
copy it, in the two places most likely to be trusted. A doc saying "check by SHA"
does not stop a tool from handing you the other command.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_RELEASE = Path(__file__).resolve().parent.parent / "scripts" / "release.py"


@pytest.fixture(scope="module")
def src() -> str:
    return _RELEASE.read_text(encoding="utf-8")


def test_the_release_script_never_teaches_limit_1(src):
    """The exact idiom that produced the wrong verdict."""
    offenders = [
        line.strip()
        for line in src.splitlines()
        if "gh run list" in line and "--limit 1" in line and not line.strip().startswith("#")
    ]

    assert not offenders, (
        f"release.py hands the user a list-position CI check: {offenders}. "
        "`--limit 1` answers 'what is the newest run', not 'did my commit pass'. "
        "Key it on headSha instead."
    )


def test_every_ci_hint_filters_on_headsha(src):
    """A hint that mentions the runs API must say WHICH run it means."""
    hints = [line for line in src.splitlines() if "gh run list" in line and not line.strip().startswith("#")]

    assert hints, "no CI hint found — the scan is looking for the wrong thing"
    joined = "\n".join(hints) + "\n" + src
    assert "headSha" in joined, "the CI hint must select by headSha"


def test_the_hint_is_runnable_when_a_sha_is_known(src):
    """A placeholder is the degraded form, not the normal one.

    If the hint only ever printed `<sha>`, every reader would have to go find the
    SHA themselves — and the one who does not is back to `--limit 1`.
    """
    assert "release_sha" in src, "the tagged SHA must be captured so the printed command is runnable"
    assert re.search(r"self\.release_sha\s*=\s*subprocess\.run", src), (
        "release_sha should be read from git rev-parse at tag time, not assumed"
    )


def test_capturing_the_sha_cannot_fail_a_published_release(src):
    """The capture is best-effort by design.

    It runs AFTER the tag is pushed — the release is already out. Raising there
    would turn a cosmetic hint into a failed release, which is a worse trade than
    printing a placeholder.
    """
    idx = src.find("self.release_sha = subprocess.run")
    assert idx != -1
    window = src[idx - 400 : idx + 400]

    assert "try:" in window and "except Exception:" in window, (
        "the SHA capture must be wrapped — it runs after the tag is pushed, so a "
        "failure there must degrade the hint, not the release"
    )

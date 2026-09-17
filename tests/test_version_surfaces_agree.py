"""Every hand-written version string in this repo must say the same thing.

Measured 2026-09-17 on one box, for the single package `empirica_mcp`:

    __version__ in __init__.py   1.8.14    <- wrong in SOURCE and in every install
    dist-info METADATA           1.13.46   <- what `doctor` reads
    pyproject.toml               1.13.47
    diff installed vs source     .py files BYTE-IDENTICAL

The code was current and every version surface misstated it, each in a different
direction. `__version__ = "1.8.14"` had drifted roughly five minors because the
release sweep never listed that file — a hand-maintained string nobody maintained.

That was the evening a fleet was trying to establish which code it was running,
and the lesson came in two halves: an IDENTICAL version string is not evidence of
identical code, and a DIFFERING one is not evidence of different code either.

This guard is deliberately about the repo, where it can be exact. It cannot say
anything about what is installed — that is `doctor --deploy-gaps`' job — but it
stops the source itself from lying before anything is built from it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version(path: Path) -> str:
    m = re.search(r'^version\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    assert m, f"no version in {path}"
    return m.group(1)


def _dunder_version(path: Path) -> str:
    m = re.search(r'^__version__\s*=\s*"([^"]+)"', path.read_text(encoding="utf-8"), re.M)
    assert m, f"no __version__ in {path}"
    return m.group(1)


CORE = _pyproject_version(_ROOT / "pyproject.toml")

SURFACES = [
    ("core __init__", lambda: _dunder_version(_ROOT / "empirica" / "__init__.py")),
    ("mcp pyproject", lambda: _pyproject_version(_ROOT / "empirica-mcp" / "pyproject.toml")),
    ("mcp __init__", lambda: _dunder_version(_ROOT / "empirica-mcp" / "empirica_mcp" / "__init__.py")),
]


@pytest.mark.parametrize(("name", "read"), SURFACES, ids=[s[0] for s in SURFACES])
def test_every_version_surface_matches_core(name, read):
    assert read() == CORE, (
        f"{name} says {read()} while core's pyproject says {CORE}. A version string that "
        "disagrees with its neighbours makes 'which code am I running' unanswerable."
    )


def test_the_release_sweep_covers_every_surface_this_test_reads():
    """The reason 1.8.14 survived: the sweep did not list the file.

    Asserting the VALUES match today says nothing about tomorrow's bump. This
    asserts the mechanism — every file read above is a file release.py rewrites.
    """
    sweep = (_ROOT / "scripts" / "release.py").read_text(encoding="utf-8")

    for needle in ('"empirica" / "__init__.py"', '"empirica_mcp" / "__init__.py"', '"empirica-mcp" / "pyproject.toml"'):
        assert needle in sweep, f"release.py does not sweep {needle} — it will drift exactly as 1.8.14 did"

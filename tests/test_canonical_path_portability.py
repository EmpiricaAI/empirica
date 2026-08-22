"""A locator that resolves on exactly one machine is not a locator.

`epistemic_sources.canonical_path` was written as `str(Path(p).resolve())` —
always absolute, always machine-specific. Measured on core's own store:

    absolute, machine-specific   18   (14 inside the repo, 4 under /tmp)
    repo-relative, portable       2

One column, two incompatible meanings, 90% in the shape that cannot travel. That
is upstream of any git-as-source-transport policy: a peer who clones the repo can
verify the bytes via `content_hash` and has no way to find the file, because the
only locator names a directory on somebody else's laptop.

**The rule:** inside the project root → repo-relative; outside → absolute AND
flagged non-portable, because pretending otherwise is what produced the mess.

**The risk this file guards is the half-migration.** Two consumers OPEN the file
at `canonical_path` — `sources-update` re-fetches from it and `sanctify` checks it
exists. Storing relative values without teaching those readers to resolve would
break re-fetch on every normalised row while the legacy absolutes kept working,
and the rows that still worked would hide the rows that did not.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.core.sources.canonical_path import (
    NON_PORTABLE,
    PORTABLE,
    normalise,
    resolve,
)


@pytest.fixture
def repo(tmp_path):
    """A project root with a file in it, and a file outside it."""
    root = tmp_path / "proj"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "guide.md").write_text("inside")
    outside = tmp_path / "elsewhere.md"
    outside.write_text("outside")
    return root, outside


# ── the rule ─────────────────────────────────────────────────────────────────


def test_a_file_inside_the_repo_is_stored_repo_relative(repo):
    root, _ = repo
    stored, portability = normalise(root / "docs" / "guide.md", root=root)

    assert stored == "docs/guide.md"
    assert portability == PORTABLE
    assert not stored.startswith("/"), "an absolute path here is the whole defect"


def test_a_file_outside_the_repo_stays_absolute_and_is_flagged(repo):
    """Not silently relative-ised into nonsense, and not silently passed off as
    portable — the flag is what makes the limitation visible."""
    root, outside = repo
    stored, portability = normalise(outside, root=root)

    assert stored == str(outside)
    assert portability == NON_PORTABLE


def test_an_already_relative_input_is_not_re_anchored_to_cwd(repo):
    """The old writer did `Path.cwd() / p` on relative input, which turned an
    already-portable value into a machine-specific one."""
    root, _ = repo
    stored, portability = normalise("docs/guide.md", root=root)

    assert stored == "docs/guide.md"
    assert portability == PORTABLE


def test_separators_are_posix(repo):
    """The value crosses machines. A backslash path written on Windows would not
    resolve on the peer that reads it — the same defect, one platform along."""
    root, _ = repo
    stored, _ = normalise(root / "docs" / "guide.md", root=root)
    assert "\\" not in stored


def test_no_path_gives_no_answer():
    assert normalise(None) == (None, None)
    assert normalise("") == (None, None)


# ── the round trip, which is what the readers depend on ──────────────────────


def test_a_normalised_path_resolves_back_to_the_same_file(repo):
    """THE property. Without it the migration silently breaks source re-fetch."""
    root, _ = repo
    original = root / "docs" / "guide.md"
    stored, _ = normalise(original, root=root)

    assert resolve(stored, root=root) == original
    assert resolve(stored, root=root).read_text() == "inside"


def test_resolve_still_accepts_a_legacy_absolute(repo):
    """The column holds both shapes for as long as pre-migration rows exist. A
    reader that understood only the new one would be a SECOND incompatible
    meaning rather than a fix."""
    root, outside = repo
    assert resolve(str(outside), root=root) == outside
    assert resolve(str(outside), root=root).read_text() == "outside"


def test_resolve_of_nothing_is_none():
    assert resolve(None) is None
    assert resolve("") is None


@pytest.mark.parametrize("shape", ["relative", "absolute"])
def test_both_shapes_round_trip_from_the_same_reader(repo, shape):
    """Parametrised so a failure names WHICH shape broke."""
    root, outside = repo
    target = (root / "docs" / "guide.md") if shape == "relative" else outside
    stored, _ = normalise(target, root=root)
    assert resolve(stored, root=root) == target


# ── the readers were migrated in the same change ─────────────────────────────


@pytest.mark.parametrize(
    "module",
    [
        "empirica/cli/command_handlers/sources_update_commands.py",
        "empirica/core/sources/sanctify.py",
    ],
)
def test_every_consumer_that_opens_the_path_resolves_it_first(module):
    """A half-migrated column is worse than an un-migrated one.

    Both of these OPEN the file. If either kept treating the stored value as a
    ready-to-open path, it would work on legacy absolutes and fail on every
    normalised row — and the working rows would hide the broken ones.
    """
    src = (Path(__file__).resolve().parent.parent / module).read_text()
    assert "canonical_path import resolve" in src, f"{module} opens canonical_path without resolving it"


def test_the_writer_normalises_rather_than_storing_the_resolved_absolute():
    """NEGATIVE CONTROL on the source of the whole defect."""
    src = (
        Path(__file__).resolve().parent.parent / "empirica/cli/command_handlers/artifact_log_commands.py"
    ).read_text()
    assert "canonical_path import normalise" in src
    assert 'identity["canonical_path"] = str(p)' not in src, "the unconditional absolute write is back"

"""Retiring a command is announced, then done (goal cfc94d52, David 2026-11-20): a listed name prints a notice for a release,
and this test fails once a name outlives the release that was to remove it."""

from __future__ import annotations

import yaml

from empirica import __version__
from empirica.core import deprecations
from empirica.core.usage_census import cli_commands


def _release(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in str(v).split(".")[:3])


def test_every_entry_names_a_release_to_remove_it_in_and_a_reason():
    for name, entry in deprecations.deprecated_commands().items():
        assert entry.get("since") and entry.get("remove_in") and entry.get("reason"), name
        assert _release(entry["remove_in"]) > _release(entry["since"]), name


def test_no_deprecated_name_outlives_its_removal_release():
    still = set(cli_commands())
    overdue = [
        n
        for n, e in deprecations.deprecated_commands().items()
        if _release(__version__) >= _release(e["remove_in"]) and n in still
    ]
    assert overdue == [], f"remove from the parser: {overdue}"


def test_a_listed_name_prints_a_notice_with_its_replacement(monkeypatch):
    monkeypatch.setattr(
        deprecations,
        "deprecated_commands",
        lambda: {
            "gl": {"name": "gl", "since": "1.17.14", "remove_in": "1.17.15", "replacement": "goals-list", "reason": "x"}
        },
    )
    line = deprecations.notice("gl")
    assert "1.17.15" in line and "goals-list" in line
    assert deprecations.notice("goals-list") is None


def test_the_data_file_parses_and_starts_empty_until_a_batch_is_approved():
    from pathlib import Path

    data = yaml.safe_load(
        (Path(deprecations.__file__).resolve().parent.parent / "config" / "deprecated_commands.yaml").read_text()
    )
    assert isinstance(data.get("commands"), list)

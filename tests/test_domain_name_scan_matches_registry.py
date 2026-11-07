"""PREFLIGHT's quick domain-name scan and the domain registry read a user's domain name the same way.

The scan's pattern stopped at the first character outside [A-Za-z0-9_-], so a user file with `domain: legal.eu` was known to
project-update (the registry) as `legal.eu` and to PREFLIGHT (the scan) as `legal`: PREFLIGHT ignored the project's domain and
the session-start note called it free text every session (pre-publish sweep, 1.14.22).
"""

from __future__ import annotations

import pytest

from empirica.config.domain_registry import DomainRegistry, known_domain_names

BODY = "criticality_levels:\n  medium:\n    checks: []\n"


@pytest.mark.parametrize(
    "line, name",
    [
        ("domain: legal.eu", "legal.eu"),
        ('domain: "Finance Ops"', "Finance Ops"),
        ("domain: ops   # ours", "ops"),
        ("domain: 'x-1'", "x-1"),
    ],
)
def test_the_scan_reads_a_user_domain_name_as_the_registry_does(tmp_path, monkeypatch, line, name):
    home = tmp_path / "home"
    (home / ".empirica" / "domains").mkdir(parents=True)
    (home / ".empirica" / "domains" / "mine.yaml").write_text(f"{line}\n{BODY}")
    monkeypatch.setenv("HOME", str(home))

    registry = set(DomainRegistry().list_domains())
    assert name in known_domain_names()
    assert (
        known_domain_names() == registry or name in registry
    )  # the registry may refuse a malformed file; the scan must not invent a different name


def test_a_hash_without_space_before_it_is_part_of_the_name(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".empirica" / "domains").mkdir(parents=True)
    (home / ".empirica" / "domains" / "c.yaml").write_text(f"domain: c#lang\n{BODY}")
    monkeypatch.setenv("HOME", str(home))

    assert "c#lang" in known_domain_names()

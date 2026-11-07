"""`project-init --force` keeps hand-set fields, but a value given on its command line is not dropped.

The repair merge lets existing values win so curated identity survives a re-init. A fresh init writes `domain: ''`, so
`project-init --force --domain research` afterwards kept the empty string and reported success (pre-publish sweep, 1.14.22).
"""

from __future__ import annotations

from empirica.cli.command_handlers import project_init as pi


def test_an_explicit_flag_wins_over_the_existing_value():
    template = {"name": "p", "domain": "research", "type": "research", "description": "Reads papers"}
    existing = {"name": "p", "domain": "", "description": "p project", "org_id": "org-1"}
    explicit = {"domain": "research", "type": "research", "description": "Reads papers"}

    merged = pi._merge_preserving_existing(template, existing, explicit=explicit)

    assert merged["domain"] == "research" and merged["type"] == "research" and merged["description"] == "Reads papers"
    assert merged["org_id"] == "org-1"  # curated fields still survive


def test_without_explicit_flags_existing_values_still_win():
    merged = pi._merge_preserving_existing({"domain": "default", "x": 1}, {"domain": "operations"})

    assert merged == {"domain": "operations", "x": 1}


def test_the_explicit_values_come_only_from_flags_that_were_given():
    class Args:
        type = None
        domain = "research"
        project_description = None

    assert pi._explicit_init_values(Args()) == {"domain": "research"}

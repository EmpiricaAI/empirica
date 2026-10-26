"""Every `cron:` example the docs show must be one the scheduler can install (goal 1cd283ff).

The cockpit docs and the config module's docstring showed `cron: "8,23,38,53 * * * *"`. Both the systemd and the launchd mapper refuse a
list (and a range, and a step) on purpose: a schedule they cannot express must raise rather than silently become a different timer. So a
user who copied the documented example got a hard error. Sub-daily schedules belong to `kind: interval`; a cron loop is a fixed minute and
hour with wildcard day, month and weekday.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from empirica.core.loop_scheduler.launchd import cron_to_launchd_calendar
from empirica.core.loop_scheduler.systemd import cron_to_systemd_oncalendar

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = [
    ROOT / "docs" / "architecture" / "COCKPIT.md",
    ROOT / "empirica" / "core" / "cockpit" / "project_cockpit_config.py",
]
CRON_EXAMPLE = re.compile(r"""\bcron:\s*["']([^"']+)["']""")


def _examples() -> list[tuple[str, str]]:
    found = []
    for path in DOCUMENTS:
        for match in CRON_EXAMPLE.finditer(path.read_text(encoding="utf-8")):
            found.append((path.name, match.group(1)))
    return found


def test_the_scan_finds_the_examples_it_guards():
    """Control: a scan that matches nothing passes forever."""
    assert len(_examples()) >= 2, _examples()


@pytest.mark.parametrize(("document", "cron"), _examples())
def test_a_documented_cron_example_installs_on_both_schedulers(document, cron):
    cron_to_systemd_oncalendar(cron)
    cron_to_launchd_calendar(cron)


def test_the_mappers_still_refuse_a_list_so_the_examples_cannot_be_fixed_by_loosening_them():
    """Control: the refusal is the design; the docs move, the mapper does not."""
    with pytest.raises(ValueError):
        cron_to_systemd_oncalendar("8,23,38,53 * * * *")
    with pytest.raises(ValueError):
        cron_to_launchd_calendar("8,23,38,53 * * * *")

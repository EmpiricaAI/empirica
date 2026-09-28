"""doctor reports transaction files left open long after anything could be using them.

ecodex (2026-09-28) had active_transaction_tmux_48.json open since 2026-07-25.
Hooks locate a pane's transaction by the instance suffix, so a pane that later
reuses tmux_48 would count its work against a two-month-old window.
"""

from __future__ import annotations

import json
from pathlib import Path

from empirica.cli.command_handlers.doctor import PASS, SKIP, WARN, check_stale_open_transactions

NOW = 1_790_000_000.0
DAY = 86400


def _tx(root: Path, suffix: str, status: str, age_days: float) -> None:
    (root / ".empirica").mkdir(parents=True, exist_ok=True)
    (root / ".empirica" / f"active_transaction{suffix}.json").write_text(
        json.dumps(
            {"transaction_id": f"tx{suffix}-0000", "status": status, "preflight_timestamp": NOW - age_days * DAY}
        )
    )


def test_an_old_open_transaction_is_reported_with_the_way_to_close_it(tmp_path):
    _tx(tmp_path, "_tmux_48", "open", 65)
    _tx(tmp_path, "_tmux_12", "closed", 90)  # closed: however old, not a problem
    _tx(tmp_path, "_tmux_2", "open", 0.1)  # open and fresh: someone is working

    c = check_stale_open_transactions(tmp_path, now=NOW)

    assert c.status == WARN
    assert [r["instance"] for r in c.data["stale"]] == ["tmux_48"]
    assert "3 transaction file(s) read, 2 open" in c.detail
    assert "transaction-adopt --from tmux_48" in c.hint


def test_fresh_and_closed_files_pass_and_say_what_was_read(tmp_path):
    _tx(tmp_path, "_tmux_2", "open", 1)
    _tx(tmp_path, "_tmux_12", "closed", 90)

    c = check_stale_open_transactions(tmp_path, now=NOW)

    assert c.status == PASS
    assert "2 transaction file(s) read, 1 open" in c.detail


def test_no_empirica_directory_is_a_skip(tmp_path):
    assert check_stale_open_transactions(tmp_path, now=NOW).status == SKIP

"""goals-list --output json names the remedy when it truncates, not only the facts.

empirica-workspace, prop_g6biqcvzoraklgokljypyptihe: the human header said
"CAPPED ... use --uncapped", while the JSON carried `truncated` and
`total_matching` and never mentioned --uncapped. The AI reads the JSON.
"""

from __future__ import annotations

from empirica.cli.command_handlers.goal_commands import _truncation_notice


def test_a_capped_list_names_every_remedy():
    notice = _truncation_notice(3, 54, 3)
    assert notice is not None
    assert "showing 3 of 54" in notice
    for remedy in ("--uncapped", "--all-projects", "--limit"):
        assert remedy in notice


def test_a_complete_list_carries_no_notice():
    assert _truncation_notice(5, 5, 20) is None
    assert _truncation_notice(0, None, 20) is None, "an uncounted total is not a truncation"

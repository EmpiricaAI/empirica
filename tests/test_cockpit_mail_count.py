"""M counts the mail a practice still owes a reply, and the line under a pane shows only what needs attention.

From David's review of the TV cockpit (prop_vc67kjtw4vfj3b5uavfqyjsycu, prop_xlduifbf5vdenieiq2hrbk4jk4): the inbox count was
everything accepted and never archived (outreach read 108), the same under every pane of a window; the raw-JSON inbox popup was
unreadable, so I became part of M, whose wake makes the practice read its own mail; S joined U in the menu only.
"""

from __future__ import annotations

import json
import subprocess

from empirica.core.cockpit import bar


def _run(inbox, outbox):
    def run(argv, **_kw):
        proposals = outbox if "--outbox" in argv else inbox
        return subprocess.CompletedProcess(argv, 0, json.dumps({"proposals": proposals}), "")

    return run


ME = "empirica.david.empirica-web"


def test_only_mail_that_still_needs_this_practice_s_reply_counts():
    inbox = [
        {"id": "p1", "target_claudes": [ME]},  # waiting
        {"id": "p2", "target_claudes": [ME]},  # answered below with a reply that kept the thread open
        {"id": "p3", "target_claudes": [ME], "is_archived": True},  # filed away
        {
            "id": "p4",
            "target_claudes": [ME, "empirica.david.empirica-nle"],
            "pending_targets": ["empirica.david.empirica-nle"],
        },
        {
            "id": "p5",
            "target_claudes": [ME, "empirica.david.empirica-nle"],
            "pending_targets": [ME],
        },  # its part still open
    ]
    outbox = [{"id": "r1", "parent_id": "p2"}, {"id": "r2", "parent_id": None}]
    assert bar.mail_count("empirica-web", "/x", run=_run(inbox, outbox)) == 2  # p1 and p5


def test_a_mailbox_that_cannot_be_read_is_unknown_not_empty():
    def broken(argv, **_kw):
        return subprocess.CompletedProcess(argv, 1, "", "cortex unreachable")

    assert bar.mail_count("empirica-web", "/x", run=broken) is None
    assert bar.mail_count("empirica-web", "/x", run=_run([], [])) == 0


def test_the_m_chip_reads_listener_and_mail_together():
    assert bar.mail_chip(("red", "listener down", True), 4) == ("red", "listener down · mail 4")
    assert bar.mail_chip(("orange", "listener ×2", True), 0) == ("orange", "listener ×2")
    assert bar.mail_chip(("", "", True), 3) == ("orange", "mail 3")
    assert bar.mail_chip(("", "", True), None) == ("", "mail ?")
    assert bar.mail_chip(("", "", False), None) == ("", ""), "a practice not on the mesh has no mailbox to fail to read"


def test_the_line_is_the_practice_then_only_coloured_chips_then_menu_and_help():
    line = bar.commands_format()
    assert line.startswith(bar.PRACTICE_BLOCK)
    for key, label in bar.CHIPS:
        chip = bar.chip_format(key, label)
        assert bar.shown_chip_format(key, label) in line and chip in bar.shown_chip_format(key, label)
        assert "==:" in bar.shown_chip_format(key, label), f"{key} is shown only when red or orange"
    assert line.index("☰ commands") > line.index(bar.shown_chip_format("L", "login")) and line.endswith(
        "? help #[default] "
    )


def test_s_and_u_are_menu_only_and_the_inbox_key_is_retired():
    assert "S" not in dict(bar.CHIPS) and "S" in bar.MENU_ONLY and "U" in bar.MENU_ONLY
    assert "I" not in dict(bar.CHIPS) and "I" in bar.RETIRED_KEYS
    args = bar.menu_args("P")
    assert any(" chip S " in a for a in args), "S is still in the menu"
    assert not any(" chip I " in a for a in args)
    assert any(a.startswith("M  mail: wake it to read its mail") for a in args)


def test_an_unreadable_or_cut_short_outbox_is_unknown_not_unanswered():
    inbox = [{"id": "p1", "target_claudes": [ME], "created_at": "2026-11-14T00:00:00Z"}]

    def outbox_fails(argv, **_kw):
        if "--outbox" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "timeout")
        return subprocess.CompletedProcess(argv, 0, json.dumps({"proposals": inbox}), "")

    assert bar.mail_count("empirica-web", "/x", run=outbox_fails) is None

    def outbox_truncated(argv, **_kw):
        body = {"proposals": [], "matched": 450} if "--outbox" in argv else {"proposals": inbox}
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    assert bar.mail_count("empirica-web", "/x", run=outbox_truncated) is None


def test_the_outbox_is_read_only_from_the_oldest_waiting_message():
    seen = []
    inbox = [{"id": "p1", "created_at": "2026-11-10T00:00:00Z"}, {"id": "p2", "created_at": "2026-11-12T00:00:00Z"}]

    def run(argv, **_kw):
        seen.append(argv)
        body = {"proposals": []} if "--outbox" in argv else {"proposals": inbox}
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    assert bar.mail_count("empirica-web", "/x", run=run) == 2
    (outbox_call,) = [a for a in seen if "--outbox" in a]
    assert outbox_call[outbox_call.index("--since") + 1] == "2026-11-10T00:00:00Z"

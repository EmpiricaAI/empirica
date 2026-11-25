"""An FYI leaves the cockpit mail chip once the practice has read it (goal f5148923, prop_4ca3xu5p).

The chip counted every accepted, unarchived message not yet answered, so an FYI collab_brief, which needs no reply, stayed
counted until archived; cortex showed 29 that way. David, 2026-11-25: FYIs leave once read. Cortex keeps no read marker, so
`mailbox show` records one locally per practice. Other kinds still count until answered.
"""

from __future__ import annotations

import json
import subprocess

from empirica.core import mailbox_seen
from empirica.core.cockpit import bar


def test_mark_and_read_back(tmp_path):
    mailbox_seen.mark_seen("empirica", "prop_a", home=tmp_path)
    assert mailbox_seen.seen_ids("empirica", home=tmp_path) == {"prop_a"}
    assert mailbox_seen.seen_ids("cortex", home=tmp_path) == set()


def test_the_store_is_capped(tmp_path):
    for i in range(mailbox_seen.MAX_IDS + 5):
        mailbox_seen.mark_seen("p", f"prop_{i}", home=tmp_path)
    ids = mailbox_seen.seen_ids("p", home=tmp_path)
    assert len(ids) == mailbox_seen.MAX_IDS and f"prop_{mailbox_seen.MAX_IDS + 4}" in ids


def _run(inbox):
    def run(argv, **kw):
        body = {"proposals": [] if "--outbox" in argv else inbox}
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(body), stderr="")

    return run


def test_a_read_fyi_leaves_the_count_and_a_read_request_stays(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    inbox = [
        {"id": "prop_fyi", "type": "collab_brief", "created_at": "2026-11-25T10:00:00"},
        {"id": "prop_req", "type": "code_change_request", "created_at": "2026-11-25T10:00:00"},
        {"id": "prop_new", "type": "collab_brief", "created_at": "2026-11-25T10:00:00"},
    ]
    assert bar.mail_count("empirica", str(tmp_path), run=_run(inbox)) == 3
    mailbox_seen.mark_seen("empirica", "prop_fyi")
    mailbox_seen.mark_seen("empirica", "prop_req")
    assert bar.mail_count("empirica", str(tmp_path), run=_run(inbox)) == 2

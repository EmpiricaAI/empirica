"""Archive must name WHOSE mailbox, or broadcast threads are unarchivable forever.

Cortex refuses to archive a proposal when more than one of the caller's
practices participates in it, and the refusal is correct — clearing all of them
from one call hides the thread from practitioners that never acted:

    400 ambiguous archive: 3 of your practices participate in this proposal
        (empirica.david.empirica, empirica.david.empirica-cortex,
         empirica.david.empirica-mesh-support). Pass ai_id to say WHOSE mailbox
        to archive

The client could not satisfy it. `mailbox archive` had no `--ai-id` flag and its
request body never carried the field, so the error instructed the operator to
pass something nothing could pass — an **unrecoverable gate**. Multi-participant
proposals are not an edge case: they are the normal shape of every cortex and
mesh-support broadcast, so the practical effect was an inbox that could not be
cleared, with eight live proposals stuck behind it.

The `reply` path had the same hole one level quieter: it auto-archives the
parent after closing it, so a reply to any broadcast thread returned
`parent_closed: true, parent_archived: false` — a half-completed handshake with
no reason attached that a reader could act on.
"""

from __future__ import annotations

from argparse import Namespace

from empirica.cli.command_handlers.mailbox_commands import handle_mailbox_archive_command

CREDS = ("https://cortex.example", "test-key")
# Shaped to the REAL roster the resolver walks: tenants -> projects, keyed by
# `slug`/`ai_id_short` with the canonical form in `ai_id_mesh`. The first draft
# of this fixture invented a `practices` list and the test failed against
# working code — a fixture that disagrees with production is the failure mode
# `_resolve_canonical_ai_id` was itself written to document.
ROSTER = {
    "self": {"tenant_slug": "david"},
    "org": {
        "tenants": [
            {
                "tenant_slug": "david",
                "projects": [{"slug": "empirica", "ai_id_mesh": "empirica.david.empirica"}],
            }
        ]
    },
}


def _args(**kw):
    base = {"proposal_id": "prop_abc", "reason": None, "ai_id": None, "output": "json"}
    base.update(kw)
    return Namespace(**base)


def test_archive_sends_ai_id_so_cortex_need_not_guess(capsys):
    sent: dict = {}

    def _post(url, body, api_key, timeout):
        sent.update(body)
        return 200, {"ok": True, "proposal_id": "prop_abc", "is_archived": True}

    rc = handle_mailbox_archive_command(
        _args(),
        _resolve_cortex_creds=lambda: CREDS,
        _http_post=_post,
        _http_get=lambda url, key, timeout: (200, ROSTER),
        _resolve_ai_id=lambda: "empirica",
    )
    capsys.readouterr()
    assert rc == 0
    assert sent.get("ai_id") == "empirica.david.empirica", (
        "archive did not name a mailbox — cortex 400s on every multi-participant proposal"
    )


def test_explicit_flag_wins_over_the_resolved_default(capsys):
    """`--ai-id` must let an operator archive from a different practice's seat."""
    sent: dict = {}

    def _post(url, body, api_key, timeout):
        sent.update(body)
        return 200, {"ok": True, "is_archived": True}

    handle_mailbox_archive_command(
        _args(ai_id="empirica.david.empirica-cortex"),
        _resolve_cortex_creds=lambda: CREDS,
        _http_post=_post,
        _http_get=lambda url, key, timeout: (200, ROSTER),
        _resolve_ai_id=lambda: "empirica",
    )
    capsys.readouterr()
    assert sent.get("ai_id") == "empirica.david.empirica-cortex"


def test_unresolvable_ai_id_is_OMITTED_not_sent_as_null(capsys):
    """A null `ai_id` is a different request from an absent one.

    Single-participant archives are unambiguous and must keep working on a box
    that cannot reach the roster. Sending an explicit null would ask cortex to
    disambiguate against nothing, converting a working call into a 400 — trading
    one unrecoverable gate for another.
    """
    sent: dict = {}

    def _post(url, body, api_key, timeout):
        sent.update(body)
        sent["_keys"] = sorted(body)
        return 200, {"ok": True, "is_archived": True}

    handle_mailbox_archive_command(
        _args(),
        _resolve_cortex_creds=lambda: CREDS,
        _http_post=_post,
        _http_get=lambda url, key, timeout: (500, {}),  # roster unreachable
        _resolve_ai_id=lambda: "empirica",
    )
    capsys.readouterr()
    assert "ai_id" not in sent["_keys"], "sent an explicit null ai_id instead of omitting the field"


def test_reply_autoarchive_also_names_the_mailbox():
    """The quieter half of the same gap.

    `reply` closes the parent then archives it. Without `ai_id` that second step
    fails on every broadcast thread and surfaces only as
    `parent_archived: false` — which reads like a policy choice, not a failure.
    """
    import inspect

    from empirica.cli.command_handlers import mailbox_commands

    src = inspect.getsource(mailbox_commands.handle_mailbox_reply_command)
    # Bounded by the next statement, not by the first `}` — the reason string is
    # an f-string carrying `{new_proposal_id}`, so splitting on a brace ends the
    # slice before the field under test and the assertion silently checks
    # nothing. Same class of defect as the one this file is about.
    body = src.split("archive_body = {")[1].split("a_status")[0]
    assert '"ai_id"' in body, "reply's auto-archive omits ai_id and silently no-ops on broadcast threads"

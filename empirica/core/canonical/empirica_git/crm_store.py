"""CRM-over-git-notes TRANSPORT. Core moves bytes; workspace owns meaning.

The lane, ratified by David: `empirica-workspace` owns CRM semantics — the data,
the merge rules, conflict reporting, what a scope column means. Core owns the
transport only. So this module reads and writes an opaque envelope and knows
nothing about organizations, contacts or engagements beyond the fact that
`table` is a string the sender chose.

**Why `table` is not validated against a list here.** Workspace asked me to
import their ``sync_payload.SYNCABLE_TABLES`` rather than restate it, because a
restated copy drifts toward "your writer refuses a table my builder emits". The
import is not available — ``empirica_workspace`` is a separate distribution and
is not importable from a core install. Restating the list would be exactly the
drift they warned about, one indirection worse, so this transport **validates
neither the table name nor the row shape**. It refuses only what makes a note
unreadable as an envelope: a missing version, seat, table or row.

That is not a gap. A transport that owns the table vocabulary owns the CRM, and
the whole point of the split is that it does not. Workspace's invariant —
syncable IFF the table has a scope column — is enforced where the scope column
lives: on their side, at build and at apply. A packet naming a table their
reader rejects is a workspace-side refusal with a workspace-side reason, which
is the only place that judgment can be made correctly.

Envelope, settled 2026-09-03 and amended 2026-09-04 (their 246cce5):

    envelope_version   int, starts at 1
    sender_seat        canonical 3-form; the row cannot supply it (no CRM table
                       has an `updated_by` column, so a conflict report built
                       from row data alone cannot name who overwrote)
    table              engagements | organizations | contacts — carried as DATA
    row                the whole row
    updated_at         sender's new stamp
    base_updated_at    the stamp the sender edited from (LWW base)

Deletes are refused explicitly rather than represented, per the settlement: a
tombstone nobody agreed on is worse than an absent row.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Bumped only when a reader must change to understand a packet. Additive
#: fields (workspace's slice-2 per-group stamps) do NOT bump it — that promise
#: is exactly what the field makes verifiable: without it, an OLD note
#: predating a field is indistinguishable from a NEW note omitting it.
ENVELOPE_VERSION = 1

#: Every field a reader needs before it can route the packet at all.
REQUIRED_FIELDS = ("envelope_version", "sender_seat", "table", "row", "updated_at")

#: Where THIS seat's emissions live. Rides `refs/notes/empirica/*`, so outgoing
#: CRM notes replicate through the refspec that already exists.
NOTES_PREFIX = "empirica/crm"

#: Where a PEER's fetched notes land. Deliberately outside `empirica/*`: fetching
#: a peer's copy over the outgoing namespace would destroy the record of what
#: this seat emitted, which is exactly what the sender's sync state reconciles
#: against. Two namespaces, two questions — "what did I send" and "what arrived"
#: — and collapsing them loses the first.
INCOMING_PREFIX = "incoming/crm"


class CrmEnvelopeError(ValueError):
    """The packet cannot be read as an envelope. Never raised for CRM content."""


def build_envelope(
    *,
    sender_seat: str,
    table: str,
    row: dict[str, Any],
    updated_at: float | str,
    base_updated_at: float | str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble one envelope. Additive `extra` rides through untouched.

    `extra` exists so workspace can add slice-2 per-group stamps without core
    releasing — the additive promise made concrete rather than asserted.
    """
    if not sender_seat or sender_seat.count(".") < 2:
        raise CrmEnvelopeError(
            f"sender_seat must be the canonical 3-form <org>.<tenant>.<project>, got {sender_seat!r}. "
            "It is not retrofittable: notes written without it are anonymous forever, and no CRM "
            "table carries an updated_by column to reconstruct it from."
        )
    if not table or not isinstance(table, str):
        raise CrmEnvelopeError(f"table must be a non-empty string, got {table!r}")
    if not isinstance(row, dict) or not row:
        raise CrmEnvelopeError("row must be the whole row as a non-empty object")

    envelope = {
        "envelope_version": ENVELOPE_VERSION,
        "sender_seat": sender_seat,
        "table": table,
        "row": row,
        "updated_at": updated_at,
        "base_updated_at": base_updated_at,
    }
    if extra:
        # Never let an addition silently overwrite a settled field — a packet
        # whose `table` was clobbered by an extra would route to the wrong
        # reader with no way to notice.
        clobbered = sorted(set(extra) & set(envelope))
        if clobbered:
            raise CrmEnvelopeError(f"extra fields would overwrite settled envelope fields: {clobbered}")
        envelope.update(extra)
    return envelope


def validate_envelope(payload: Any) -> dict[str, Any]:
    """Refuse what cannot be READ. Says which field, never judges CRM content."""
    if not isinstance(payload, dict):
        raise CrmEnvelopeError(f"envelope must be an object, got {type(payload).__name__}")

    missing = [f for f in REQUIRED_FIELDS if payload.get(f) is None]
    if missing:
        raise CrmEnvelopeError(f"envelope missing required field(s): {missing}")

    version = payload.get("envelope_version")
    if not isinstance(version, int):
        raise CrmEnvelopeError(f"envelope_version must be an int, got {version!r}")
    if version > ENVELOPE_VERSION:
        # Forward-incompatible, and SAID so. Silently accepting a future packet
        # would apply a row a newer sender expected different handling for.
        raise CrmEnvelopeError(
            f"envelope_version {version} is newer than this transport understands "
            f"({ENVELOPE_VERSION}) — upgrade empirica before syncing this peer's CRM notes."
        )
    if "delete" in payload or payload.get("op") == "delete":
        raise CrmEnvelopeError(
            "deletes are not carried by this transport, by agreement — a tombstone nobody "
            "agreed on is worse than an absent row. Coordinate a delete out of band."
        )
    return payload


def note_ref(table: str, row_id: str, namespace: str = NOTES_PREFIX) -> str:
    """`<namespace>/<table>/<id>` — BOTH segments are data, not literals.

    Table is a path segment from the envelope: hardcoding `engagements` would
    silently drop two of three tables while every push reported success.

    Namespace is a parameter for the same reason one level up, and I got that
    one wrong first. The original baked in `empirica/crm`, so a peer's fetched
    notes — which must land OUTSIDE the outgoing namespace to avoid destroying
    the record of what this seat emitted — were unaddressable through this API.
    Workspace's pull path therefore bypassed core's transport entirely and read
    notes via raw git, not by preference but because `read()` had no way to say
    where to look. Same defect as the table literal, one level up: I avoided
    hardcoding the value and hardcoded the container.
    """
    return f"{namespace}/{table}/{row_id}"


class CrmNoteStore:
    """Read/write CRM envelopes as git notes. No CRM knowledge beyond routing.

    `namespace` selects WHICH side of the exchange this store addresses:
    `NOTES_PREFIX` for this seat's emissions (the default, and what replicates),
    `INCOMING_PREFIX` for a peer's fetched notes. One transport, both
    directions — a store that could only address its own outgoing namespace
    forced the receive side to reimplement note reading.
    """

    def __init__(self, workspace_root: str | Path, namespace: str = NOTES_PREFIX):
        self.workspace_root = Path(workspace_root)
        self.namespace = namespace

    def _git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=self.workspace_root,
            capture_output=True,
            text=True,
            check=check,
            timeout=30,
        )

    def _head(self) -> str:
        return self._git("rev-parse", "HEAD").stdout.strip()

    def write(self, envelope: dict[str, Any], row_id: str) -> str:
        """Persist one envelope. Returns the note ref written."""
        validate_envelope(envelope)
        ref = note_ref(envelope["table"], row_id, self.namespace)
        self._git("notes", f"--ref={ref}", "add", "-f", "-m", json.dumps(envelope, indent=2), self._head())
        logger.info("CRM envelope written to %s", ref)
        return ref

    def read(self, table: str, row_id: str) -> dict[str, Any] | None:
        """Read one envelope back, or None when the ref does not exist."""
        ref = note_ref(table, row_id, self.namespace)
        proc = self._git("notes", f"--ref={ref}", "show", "HEAD", check=False)
        if proc.returncode != 0:
            return None
        try:
            return validate_envelope(json.loads(proc.stdout))
        except (json.JSONDecodeError, CrmEnvelopeError) as e:
            # A corrupt packet is a REPORTABLE state, not an absent one: silently
            # returning None here would make a malformed note indistinguishable
            # from a row that was never shared.
            logger.warning("CRM note %s is unreadable: %s", ref, e)
            raise

    def list_refs(self, table: str | None = None) -> list[str]:
        """Every CRM note ref, optionally scoped to one table."""
        prefix = f"refs/notes/{self.namespace}/{table}/" if table else f"refs/notes/{self.namespace}/"
        proc = self._git("for-each-ref", "--format=%(refname)", prefix, check=False)
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

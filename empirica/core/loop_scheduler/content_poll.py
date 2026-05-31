"""Content-aware loop tick body for cortex-mailbox-poll.

Replaces the heartbeat model ("fire every 30s, AI decides whether to act")
with a content-aware model ("poll Cortex, emit only when ECO-decided content
arrived or changed status").

## ECO-gated autonomy property

This module is the security/architecture pivot for canonical loops:

  - The systemd timer fires this poll every N seconds. The timer is
    mechanism — easy to hijack at the OS layer.
  - The poll fetches `/v1/orchestration/inbox?status=accepted,changed,declined`
    — only proposals ECO (David, via phone/extension) has decided on.
  - Diffs against last-seen state. Emits one line per new-or-changed proposal.
  - The AI's wake signal carries a `proposal_id` — the action authorization
    traces back to an ECO decision. Hijacking the timer can't widen the
    AI's authority because any forged event points at a proposal_id whose
    status the AI re-verifies with Cortex (which only returns ECO-decided
    state).

If the timer is silent (no new content), the AI stays idle — zero token
cost. If a malicious actor injects events into the fires log, the AI's
verification step against Cortex catches them.

## State file

`~/.empirica/loop_state/<inst>_<loop>.json`:

    {
      "last_poll_ts": "2026-05-15T20:00:00+00:00",
      "proposals": {
        "prop_xyz": {"status": "accepted", "seen_at": "2026-05-15T19:51:53Z"},
        "prop_abc": {"status": "changed",  "seen_at": "2026-05-15T19:30:00Z"}
      }
    }

## Bootstrap behavior

On first run (state file absent), the listener now emits all proposals
that pass the EMISSION_STATUSES filter — those are already the meaningful
wake events (accepted/changed/declined for inbox; changed/declined/completed
for outbox). The earlier "record without emit" policy was overcautious:
it prevented historical-flood, but also lost wake events for unacked
proposals pending at install time (the test case David hit 2026-05-16
when pressing Events on a fresh instance that had already-accepted
proposals in inbox). The AI's reaction protocol is idempotent
(re-verifies each proposal_id against Cortex) so a one-time emit of
~dozen items at bootstrap is benign.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


class ContentPollUnreachable(Exception):
    """Raised by poll_and_diff(raise_on_unreachable=True) when BOTH the
    inbox and outbox fetches fail — i.e. Cortex is unreachable and no wake
    events can be emitted. The listener opts into this so it can surface a
    fail-heartbeat instead of silently no-op'ing (the 10-day-deaf failure
    mode). Callers that don't pass the flag keep the graceful empty return."""


# Status filters per direction — what counts as a real wake signal.
#
# INBOX: proposals TARGETING this AI. ECO must have decided — that's the
# canonical authorization boundary (David's ECO-gated autonomy property).
# `eco_review` is excluded — AI must never act on ECO-undecided content.
EMISSION_STATUSES_INBOX = ("accepted", "changed", "declined")

# OUTBOX: proposals THIS AI emitted. ECO already decided at emission time;
# downstream state transitions are informational acks rather than auth events.
#  - 'changed'   → ECO sent back for refinement (real wake)
#  - 'declined'  → ECO rejected (real wake — update mental model)
#  - 'completed' → target AI finished the work (real wake — David's AI-to-AI
#                  ack primitive, carries commit_sha in audit_log details)
# 'accepted' on outbox is informational only (ECO approved your emission,
# target will act) — skipping prevents noise.
EMISSION_STATUSES_OUTBOX = ("changed", "declined", "completed")

# Back-compat alias (T6 code referenced EMISSION_STATUSES for inbox).
EMISSION_STATUSES = EMISSION_STATUSES_INBOX


@dataclass
class ProposalEvent:
    """One content event emitted to the fires log. AI consumes via Monitor."""
    instance_id: str
    loop_name: str
    proposal_id: str
    proposal_title: str
    status: str
    action_category: str | None
    eco_actor: str | None
    new_or_changed: str  # "new" | "status_changed"
    direction: str = "inbox"  # "inbox" | "outbox" — tells AI which reaction
    commit_sha: str | None = None  # populated when status='completed'
    bead_id: str | None = None  # cortex stamps this when payload.bead_id is
    # present (graduation contract — BEAD_COORDINATION_RECORD.md §6.5). The
    # receiving AI uses it to look up the bead + render bridge-position.
    bridge_position: str | None = None  # cortex stamps this for post-
    # graduation states (BEAD_COORDINATION_RECORD.md §6.5 amendment, doc
    # 6629265). Server-computed from proposal.status; pre-graduation states
    # (noetic / needs-graduation) are client-derived and absent here.

    def to_log_line(self) -> str:
        """JSON line for ~/.empirica/loop_fires.log."""
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        return json.dumps({
            "ts": now,
            "instance_id": self.instance_id,
            "loop": self.loop_name,
            "event_type": "proposal_event",
            "direction": self.direction,
            "proposal_id": self.proposal_id,
            "proposal_title": self.proposal_title,
            "status": self.status,
            "action_category": self.action_category,
            "eco_actor": self.eco_actor,
            "change_kind": self.new_or_changed,
            "commit_sha": self.commit_sha,
            "bead_id": self.bead_id,
            "bridge_position": self.bridge_position,
        })


def _state_path(instance_id: str, loop_name: str) -> Path:
    """`~/.empirica/loop_state/<inst>_<loop>.json` — per-loop seen state."""
    safe_inst = "".join(c if c.isalnum() or c in "-_" else "-" for c in instance_id)
    safe_loop = "".join(c if c.isalnum() or c in "-_" else "-" for c in loop_name)
    base = Path.home() / ".empirica" / "loop_state"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{safe_inst}_{safe_loop}.json"


def load_state(state_path: Path) -> dict:
    """Read the per-loop state file. Returns {} if absent or unreadable."""
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"content_poll state read failed at {state_path}: {e}")
        return {}


def save_state(state_path: Path, state: dict) -> None:
    """Atomic write — write to temp + rename, so a half-written file never
    poisons the next poll."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def _fetch_orch(
    cortex_url: str,
    api_key: str,
    ai_id: str,
    path: str,
    statuses: tuple[str, ...],
    *,
    timeout: float = 10.0,
) -> list[dict]:
    """Shared GET for /v1/orchestration/{inbox,outbox} with the same shape."""
    params = urllib.parse.urlencode({
        "ai_id": ai_id,
        "status": ",".join(statuses),
        "related": "false",  # skip per-proposal Qdrant scroll for faster polls
    })
    url = f"{cortex_url.rstrip('/')}{path}?{params}"
    req = urllib.request.Request(
        url, method="GET",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        body = json.loads(raw) if raw else {}
    proposals = body.get("proposals", [])
    return proposals if isinstance(proposals, list) else []


def fetch_cortex_inbox(
    cortex_url: str,
    api_key: str,
    ai_id: str,
    *,
    timeout: float = 10.0,
) -> list[dict]:
    """GET /v1/orchestration/inbox — proposals where target_claudes contains ai_id."""
    return _fetch_orch(cortex_url, api_key, ai_id,
                       "/v1/orchestration/inbox", EMISSION_STATUSES_INBOX,
                       timeout=timeout)


def fetch_cortex_outbox(
    cortex_url: str,
    api_key: str,
    ai_id: str,
    *,
    timeout: float = 10.0,
) -> list[dict]:
    """GET /v1/orchestration/outbox — proposals where source_claude == ai_id.

    Used for completion/refinement wake signals. The AI emitting the proposal
    is the audience: 'your work landed' (completed), 'ECO sent back' (changed),
    'ECO rejected' (declined). No ECO gate needed — ECO already decided when
    the proposal left."""
    return _fetch_orch(cortex_url, api_key, ai_id,
                       "/v1/orchestration/outbox", EMISSION_STATUSES_OUTBOX,
                       timeout=timeout)


def _proposal_status(p: dict) -> str:
    return str(p.get("status", "")).lower()


def _proposal_id(p: dict) -> str:
    return str(p.get("id") or "")


def diff_proposals(
    current: list[dict],
    last_seen: dict[str, str],
    *,
    valid_statuses: tuple[str, ...] = EMISSION_STATUSES_INBOX,
) -> list[tuple[dict, str]]:
    """Return (proposal, change_kind) for each new or status-changed item.

    change_kind ∈ {"new", "status_changed"}. Proposals whose ID + status are
    unchanged are filtered out — the AI already saw them.

    `valid_statuses` is the security filter — defaults to INBOX statuses
    (ECO-decided only). Callers polling outbox pass EMISSION_STATUSES_OUTBOX.
    """
    out: list[tuple[dict, str]] = []
    for p in current:
        pid = _proposal_id(p)
        if not pid:
            continue
        status = _proposal_status(p)
        if status not in valid_statuses:
            continue
        prior = last_seen.get(pid)
        if prior is None:
            out.append((p, "new"))
        elif prior != status:
            out.append((p, "status_changed"))
    return out


def _extract_commit_sha(p: dict) -> str | None:
    """Pull commit_sha from a 'completed' proposal's audit log.

    The completion primitive (David, 2026-05-15) appends a 'completed' audit
    entry with `details.commit_sha` so the source AI knows which commit
    landed their work. Returns None when not present (status != completed,
    or older proposals without the detail).
    """
    audit = p.get("audit_log") or []
    if not isinstance(audit, list):
        return None
    for entry in reversed(audit):  # most recent first
        if isinstance(entry, dict) and entry.get("action") == "completed":
            details = entry.get("details") or {}
            if isinstance(details, dict):
                sha = details.get("commit_sha")
                return str(sha) if sha else None
    return None


def build_event(
    p: dict, change_kind: str, instance_id: str, loop_name: str,
    *, direction: str = "inbox",
) -> ProposalEvent:
    """Compress a Cortex proposal payload into the emit shape.

    `direction` is "inbox" or "outbox" — tells the AI which reaction protocol
    applies (act on accepted vs ack a completion).
    """
    eco_decision = p.get("eco_decision") or {}
    eco_actor = eco_decision.get("actor") if isinstance(eco_decision, dict) else None
    status = _proposal_status(p)
    return ProposalEvent(
        instance_id=instance_id,
        loop_name=loop_name,
        proposal_id=_proposal_id(p),
        proposal_title=str(p.get("title", ""))[:200],
        status=status,
        action_category=str(p.get("action_category") or "") or None,
        eco_actor=str(eco_actor) if eco_actor else None,
        new_or_changed=change_kind,
        direction=direction,
        commit_sha=_extract_commit_sha(p) if status == "completed" else None,
        bead_id=_extract_bead_id(p),
        bridge_position=_extract_bridge_position(p),
    )


def _extract_bridge_position(p: dict) -> str | None:
    """Pull `bridge_position` from a proposal envelope.

    Per BEAD_COORDINATION_RECORD.md §6.5 amendment (cortex doc 6629265),
    cortex computes the proposal's bridge-position label from `proposal.status`
    and stamps it as a top-level field on the envelope for post-graduation
    states. Pre-graduation labels (`noetic` / `needs-graduation`) are
    client-derived and absent from the envelope.
    """
    bp = p.get("bridge_position")
    return str(bp) if bp else None


def _extract_bead_id(p: dict) -> str | None:
    """Pull `bead_id` from a proposal envelope (graduation contract).

    Per BEAD_COORDINATION_RECORD.md §6.5, cortex stamps `bead_id` on the
    proposal envelope when `payload.bead_id` was present at create-time.
    The stamp may surface at top level (current convention) or stay nested
    under `payload` (transitional / older payloads) — read both. Returns
    None when neither is set, so non-graduated proposals don't carry it.
    """
    bid = p.get("bead_id")
    if not bid:
        payload = p.get("payload")
        if isinstance(payload, dict):
            bid = payload.get("bead_id")
    return str(bid) if bid else None


def poll_and_diff(
    instance_id: str,
    loop_name: str,
    cortex_url: str,
    api_key: str,
    *,
    state_path: Path | None = None,
    inbox_fetch_fn=fetch_cortex_inbox,
    outbox_fetch_fn=fetch_cortex_outbox,
    raise_on_unreachable: bool = False,
) -> list[ProposalEvent]:
    """Poll both inbox + outbox, diff against last-seen, return wake events.

    Two security/event classes (David's wake-event taxonomy, 2026-05-15):

      INBOX  (target_claudes ∋ instance_id):
          ECO-decided proposals → this AI acts on them.
          EMISSION_STATUSES_INBOX = (accepted, changed, declined)
          eco_review explicitly excluded — AI must not act on ECO-undecided.

      OUTBOX (source_claude == instance_id):
          ACK-style transitions on what this AI emitted → informational.
          EMISSION_STATUSES_OUTBOX = (changed, declined, completed)
          'completed' carries commit_sha — the AI-to-AI handshake primitive.

    State file tracks both under a single proposals dict (proposal_ids are
    UUIDs, so no collision possible between inbox and outbox items).

    Bootstrap: on first run (state file absent), record everything as seen
    WITHOUT emitting — no historical-content flood when a loop is first enabled.

    `inbox_fetch_fn` / `outbox_fetch_fn` are injectable for tests.

    Returns events in stable order (all inbox events first, then outbox).
    """
    if state_path is None:
        state_path = _state_path(instance_id, loop_name)

    state = load_state(state_path)
    last_seen: dict = state.get("proposals", {})
    last_seen_statuses = {
        pid: p.get("status", "")
        for pid, p in last_seen.items()
        if isinstance(p, dict)
    } if isinstance(last_seen, dict) else {}

    # Fetch both directions; degrade gracefully on either failure.
    # Failures are logged at WARNING (not debug) — a silently-degraded
    # fetch is exactly how empirica's listener went deaf for 10 days
    # (2026-05-18 → 05-28): the fetch failed every poll, _safe swallowed
    # it at debug level, state never advanced, and nothing surfaced.
    def _safe(fn, direction: str):
        try:
            return fn(cortex_url, api_key, instance_id)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError) as e:
            detail = ""
            if isinstance(e, urllib.error.HTTPError):
                try:
                    detail = f" — HTTP {e.code}: {e.read().decode('utf-8')[:160]}"
                except Exception:
                    detail = f" — HTTP {e.code}"
            logger.warning(
                "content_poll %s fetch failed for instance=%s%s",
                direction, instance_id, detail or f": {e}",
            )
            return None

    inbox = _safe(inbox_fetch_fn, "inbox")
    outbox = _safe(outbox_fetch_fn, "outbox")
    if inbox is None and outbox is None:
        # Both failed → entire Cortex unreachable. Don't touch state.
        # Loud by design: a recurring all-fail means the listener is deaf.
        logger.warning(
            "content_poll: BOTH inbox+outbox fetches failed for instance=%s — "
            "Cortex unreachable, state NOT updated (no wake events will emit "
            "until this recovers)",
            instance_id,
        )
        if raise_on_unreachable:
            raise ContentPollUnreachable(
                f"both inbox+outbox fetches failed for instance={instance_id}"
            )
        return []
    inbox = inbox or []
    outbox = outbox or []

    # First-run vs steady-state. The original "bootstrap = skip emit"
    # rule was overcautious: it prevented historical-flood (good) but
    # also suppressed wake events for unacked items pending at install
    # time (bad — David, 2026-05-16: "press Events on fresh instance,
    # see existing pending proposal trigger wake"). Now we emit on
    # bootstrap too — EMISSION_STATUSES already filters to meaningful
    # statuses, so the worst case is a one-time emit of ~dozen items
    # the AI's reaction protocol already handles idempotently
    # (it re-verifies each proposal_id against Cortex before acting).
    inbox_diffs = diff_proposals(inbox, last_seen_statuses,
                                  valid_statuses=EMISSION_STATUSES_INBOX)
    outbox_diffs = diff_proposals(outbox, last_seen_statuses,
                                   valid_statuses=EMISSION_STATUSES_OUTBOX)

    events: list[ProposalEvent] = []
    for (p, kind) in inbox_diffs:
        events.append(build_event(p, kind, instance_id, loop_name, direction="inbox"))
    for (p, kind) in outbox_diffs:
        events.append(build_event(p, kind, instance_id, loop_name, direction="outbox"))

    # Update state — single proposals map covers both directions (UUIDs unique).
    new_proposals_map = {}
    for source_list, direction in ((inbox, "inbox"), (outbox, "outbox")):
        for p in source_list:
            pid = _proposal_id(p)
            if not pid:
                continue
            new_proposals_map[pid] = {
                "status": _proposal_status(p),
                "direction": direction,
                "seen_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
    save_state(state_path, {
        "last_poll_ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "proposals": new_proposals_map,
        "bootstrap_completed": True,
    })
    return events

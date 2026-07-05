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

    def to_log_line(self) -> str:
        """JSON line for ~/.empirica/loop_fires.log."""
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        return json.dumps(
            {
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
            }
        )


# SER coordination states an escalation can still fire for (closed is terminal).
ACTIVE_SER_STATES = frozenset({"open", "in_progress", "blocked"})


@dataclass
class EscalationEvent:
    """A recovered-on-catch-up SER escalation. Part B of the two-path
    ser_escalation hardening (autonomy prop_tr4dbwcf / prop_4wo5huw5).

    Non-proposal wakes have no proposal-store row, so a DROPPED ser_escalation
    doorbell (part A's live relay missed it — network blip, listener restart)
    is only recoverable by re-pulling the durable SER projection here. Carries
    ``via='catchup_reconcile'`` so receivers + audits can tell a recovered
    escalation from part A's live push relay.
    """

    instance_id: str
    loop_name: str
    ser_id: str
    coordination_state: str
    last_transition_at: str
    via: str = "catchup_reconcile"

    def to_log_line(self) -> str:
        """JSON line for ~/.empirica/loop_fires.log — mirrors the ser_escalation
        wake shape the /cortex-mailbox-poll reaction protocol consumes."""
        return json.dumps(
            {
                "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "instance_id": self.instance_id,
                "loop": self.loop_name,
                "event_type": "ser_escalation",
                "escalation": True,
                "source_claude": "system:ser-escalation",
                "ser_id": self.ser_id,
                "coordination_state": self.coordination_state,
                "last_transition_at": self.last_transition_at,
                "via": self.via,
            }
        )


def _normalize_ts(value) -> float | None:
    """Normalize a SER timestamp to a comparable epoch float, or None.

    Gotcha 1 (autonomy, from live use): ``last_transition_at`` / ``last_ack_at``
    arrive as epoch NUMBERS on some records, ISO STRINGS on others, and null
    when never-set. Compare only after normalizing all three shapes.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return float(s)  # numeric-as-string
            except ValueError:
                return None
    return None


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


# Per-listener resolved canonical id cache.
# Maps (cortex_url, api_key, basename) → canonical ai_id_mesh.
# Cortex's orchestration endpoints now require the canonical 3-form
# (`<org>.<tenant>.<project>`) — the bare basename returns 0 proposals.
# Resolving on every poll would add a roster fetch per call; cache by tuple
# scoped per-process. Refresh implicit on listener restart (version drift
# triggers self-relaunch, dropping the cache).
_CANONICAL_AI_ID_CACHE: dict[tuple[str, str, str], str] = {}


def _resolve_canonical_ai_id(
    cortex_url: str,
    api_key: str,
    basename: str,
    *,
    timeout: float = 10.0,
) -> str:
    """Look up the canonical 3-form (org.tenant.project) for a basename.

    Reads cortex's `/v1/users/me/roster` once per (cortex_url, api_key,
    basename) tuple, finds the row in the caller's tenant where
    `ai_id_short == basename`, returns its `ai_id_mesh`.

    On any failure → returns the basename unchanged. The listener's
    existing fetch failure path will then surface 0-result warnings, so
    the failure is loud rather than silent.
    """
    cache_key = (cortex_url, api_key, basename)
    cached = _CANONICAL_AI_ID_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        req = urllib.request.Request(
            f"{cortex_url.rstrip('/')}/v1/users/me/roster",
            method="GET",
            headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
        self_meta = body.get("self") or {}
        self_tenant = self_meta.get("tenant_slug")
        # Strict-canonical: the listener is invoked with `--instance <basename>`
        # where basename is the exact project directory name, prefix kept
        # (`empirica-cortex`, `empirica-extension`, ... or just `empirica`
        # for the root practice). The roster stores `ai_id_short` as the
        # same full slug, so a direct match suffices — no dual-form
        # candidate fallback needed.
        for tenant in (body.get("org") or {}).get("tenants", []) or []:
            if tenant.get("tenant_slug") != self_tenant:
                continue
            for proj in tenant.get("projects", []) or []:
                if proj.get("ai_id_short") == basename:
                    canonical = proj.get("ai_id_mesh") or basename
                    _CANONICAL_AI_ID_CACHE[cache_key] = canonical
                    return canonical
    except Exception as e:
        logger.warning(
            "content_poll: canonical ai_id resolution failed for %s: %s "
            "(falling back to basename — orchestration endpoints will "
            "likely return 0 proposals)",
            basename,
            e,
        )

    # Cache the basename fallback too so we don't hammer roster on every poll
    # when cortex is unreachable. Listener restart drops the cache.
    _CANONICAL_AI_ID_CACHE[cache_key] = basename
    return basename


def _fetch_orch(
    cortex_url: str,
    api_key: str,
    ai_id: str,
    path: str,
    statuses: tuple[str, ...],
    *,
    timeout: float = 10.0,
    since: str | None = None,
    limit: int | None = None,
    related: bool = False,
) -> list[dict]:
    """Shared GET for /v1/orchestration/{inbox,outbox} with the same shape.

    Resolves `ai_id` (basename) → canonical 3-form (`org.tenant.project`)
    via cortex roster before the GET. Cortex's orchestration endpoints
    require the canonical form as of 2026-06-03; the bare basename
    returns 0 proposals (silent break that left every listener deaf).

    Optional `since` (ISO-8601 incremental cursor), `limit` (server caps at
    200), and `related` (semantic-hint compute — default off for fast polls)
    are passthroughs for the `empirica mailbox poll` CLI (prop_jdldx2pz); the
    listener callers omit them and get the historical behavior unchanged.
    """
    canonical = _resolve_canonical_ai_id(cortex_url, api_key, ai_id)
    query: dict[str, str] = {
        "ai_id": canonical,
        "status": ",".join(statuses),
        "related": "true" if related else "false",  # off → skip per-proposal Qdrant scroll
    }
    if since:
        query["since"] = since
    if limit is not None:
        query["limit"] = str(limit)
    params = urllib.parse.urlencode(query)
    url = f"{cortex_url.rstrip('/')}{path}?{params}"
    req = urllib.request.Request(
        url,
        method="GET",
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
    statuses: tuple[str, ...] = EMISSION_STATUSES_INBOX,
    since: str | None = None,
    limit: int | None = None,
    related: bool = False,
    timeout: float = 10.0,
) -> list[dict]:
    """GET /v1/orchestration/inbox — proposals where target_claudes contains ai_id.

    `statuses`/`since`/`limit`/`related` are passthroughs for the CLI; the
    listener calls it positionally with just (cortex_url, api_key, ai_id) and
    gets the default emission-status filter unchanged.
    """
    return _fetch_orch(
        cortex_url,
        api_key,
        ai_id,
        "/v1/orchestration/inbox",
        statuses,
        timeout=timeout,
        since=since,
        limit=limit,
        related=related,
    )


def fetch_cortex_outbox(
    cortex_url: str,
    api_key: str,
    ai_id: str,
    *,
    statuses: tuple[str, ...] = EMISSION_STATUSES_OUTBOX,
    since: str | None = None,
    limit: int | None = None,
    related: bool = False,
    timeout: float = 10.0,
) -> list[dict]:
    """GET /v1/orchestration/outbox — proposals where source_claude == ai_id.

    Used for completion/refinement wake signals. The AI emitting the proposal
    is the audience: 'your work landed' (completed), 'ECO sent back' (changed),
    'ECO rejected' (declined). No ECO gate needed — ECO already decided when
    the proposal left."""
    return _fetch_orch(
        cortex_url,
        api_key,
        ai_id,
        "/v1/orchestration/outbox",
        statuses,
        timeout=timeout,
        since=since,
        limit=limit,
        related=related,
    )


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
    p: dict,
    change_kind: str,
    instance_id: str,
    loop_name: str,
    *,
    direction: str = "inbox",
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
    )


def _fetch_sers(cortex_url: str, api_key: str, canonical: str, *, timeout: float = 10.0) -> list[dict] | None:
    """ONE un-narrowed GET /v1/sers?ai_id=<canonical> — all participation records
    (autonomy prop_4wo5huw5, live-verified). Returns the SER projection list, or
    None on any fetch failure (caller treats None as no-op, not empty). Filtering
    to active states + the required-tier predicate is done client-side.
    """
    params = urllib.parse.urlencode({"ai_id": canonical})
    url = f"{cortex_url.rstrip('/')}/v1/sers?{params}"
    req = urllib.request.Request(url, method="GET", headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            body = json.loads(raw) if raw else {}
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("content_poll /v1/sers fetch failed for %s: %s", canonical, e)
        return None
    sers = body.get("sers", body) if isinstance(body, dict) else body
    return sers if isinstance(sers, list) else []


def reconcile_ser_escalations(
    instance_id: str,
    loop_name: str,
    cortex_url: str,
    api_key: str,
    canonical: str,
    cursor: dict,
    *,
    fetch_fn=_fetch_sers,
) -> tuple[list[EscalationEvent], dict]:
    """Recover dropped ser_escalation doorbells from the durable SER projection.

    For each ACTIVE SER where THIS instance is a REQUIRED-tier participant and
    the SER transitioned since our last ack, emit a catch-up ser_escalation —
    unless we already emitted for that exact ``(ser_id, last_transition_at)``.
    Returns ``(events, updated_cursor)``. Autonomy's 4 gotchas from live use:
      1. timestamps are mixed epoch/ISO/null → ``_normalize_ts``;
      2. participant match is EXACT canonical practice_id (no basename fallback);
      3. de-dup cursor keyed ``(ser_id, last_transition_at)`` — a re-ack self-
         clears the predicate (``last_ack_at`` advances), a new transition
         legitimately re-fires;
      4. ``via='catchup_reconcile'`` marks these vs part A's live push relay.
    """
    cursor = dict(cursor) if isinstance(cursor, dict) else {}
    events: list[EscalationEvent] = []
    if not canonical:
        return events, cursor
    sers = fetch_fn(cortex_url, api_key, canonical)
    if not sers:  # None (unreachable) or empty → no-op, preserve cursor
        return events, cursor
    for ser in sers:
        if not isinstance(ser, dict) or ser.get("coordination_state") not in ACTIVE_SER_STATES:
            continue
        ser_id = ser.get("ser_id")
        if not ser_id:
            continue
        me = next(
            (p for p in (ser.get("participants") or []) if isinstance(p, dict) and p.get("practice_id") == canonical),
            None,
        )
        if me is None or me.get("role") != "required":
            continue
        transition = _normalize_ts(ser.get("last_transition_at"))
        if transition is None:
            continue
        my_ack = _normalize_ts(me.get("last_ack_at"))
        if my_ack is not None and transition <= my_ack:
            continue  # already acked past this transition
        if cursor.get(ser_id) == transition:
            continue  # already emitted for this exact transition
        events.append(
            EscalationEvent(
                instance_id=instance_id,
                loop_name=loop_name,
                ser_id=ser_id,
                coordination_state=str(ser.get("coordination_state", "")),
                last_transition_at=str(ser.get("last_transition_at", "")),
            )
        )
        cursor[ser_id] = transition
    return events, cursor


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
) -> list[ProposalEvent | EscalationEvent]:
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
    last_seen_statuses = (
        {pid: p.get("status", "") for pid, p in last_seen.items() if isinstance(p, dict)}
        if isinstance(last_seen, dict)
        else {}
    )

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
                direction,
                instance_id,
                detail or f": {e}",
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
            raise ContentPollUnreachable(f"both inbox+outbox fetches failed for instance={instance_id}")
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
    inbox_diffs = diff_proposals(inbox, last_seen_statuses, valid_statuses=EMISSION_STATUSES_INBOX)
    outbox_diffs = diff_proposals(outbox, last_seen_statuses, valid_statuses=EMISSION_STATUSES_OUTBOX)

    events: list[ProposalEvent | EscalationEvent] = []
    for p, kind in inbox_diffs:
        events.append(build_event(p, kind, instance_id, loop_name, direction="inbox"))
    for p, kind in outbox_diffs:
        events.append(build_event(p, kind, instance_id, loop_name, direction="outbox"))

    # Part B: recover dropped ser_escalation doorbells from the durable SER
    # projection — the proposal-only diff above can't reconstruct a non-proposal
    # wake (no store row). Fail-soft: a SER-API hiccup must never break the
    # proposal catch-up path.
    ser_cursor = state.get("ser_escalations", {})
    try:
        canonical = _resolve_canonical_ai_id(cortex_url, api_key, instance_id)
        ser_events, ser_cursor = reconcile_ser_escalations(
            instance_id, loop_name, cortex_url, api_key, canonical, ser_cursor
        )
        events.extend(ser_events)
    except Exception as e:
        logger.warning("content_poll SER reconcile failed for instance=%s: %s", instance_id, e)

    # Update state — single proposals map covers both directions (UUIDs unique).
    #
    # CRITICAL: MERGE into the existing last_seen, never REPLACE.
    # The bug fixed by merging here (extension prop_e76zksrp7za5tpx3jst2kf2sau,
    # 2026-06-09): if cortex returns empty for both inbox+outbox on one poll
    # (transient empty response — different from BOTH-failed which we already
    # guard above), the rebuilt map would be `{}` and `save_state` would wipe
    # every previously-seen proposal. On the next non-empty poll, every
    # returned proposal would look "new" to `diff_proposals` because the
    # last_seen map was empty — triggering a flood of wake events for old
    # proposals that the AI already acted on and (often) archived. Cortex
    # may still return archived proposals as `accepted` in the inbox; that's
    # OK on our side as long as we remember we already saw them.
    #
    # Merging preserves last_seen across transient empty responses + tolerates
    # cortex's inbox not filtering archived. Status changes still emit
    # correctly (diff_proposals compares status, not membership).
    merged_proposals_map = dict(last_seen) if isinstance(last_seen, dict) else {}
    for source_list, direction in ((inbox, "inbox"), (outbox, "outbox")):
        for p in source_list:
            pid = _proposal_id(p)
            if not pid:
                continue
            merged_proposals_map[pid] = {
                "status": _proposal_status(p),
                "direction": direction,
                "seen_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            }
    save_state(
        state_path,
        {
            "last_poll_ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "proposals": merged_proposals_map,
            "ser_escalations": ser_cursor if isinstance(ser_cursor, dict) else {},
            "bootstrap_completed": True,
        },
    )
    return events

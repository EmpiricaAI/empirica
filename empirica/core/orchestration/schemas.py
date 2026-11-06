"""Orchestration schemas — Proposal envelope and supporting types."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

# Typed control-plane channels (prop_smjvlyt4 formalized 2026-06-26).
# When a cortex_collab carries channel ∈ this set, it bypasses the
# collab_posture mute (DoR §6: control-plane unmutable). Emit gated
# source-side to autonomy/mesh-support practices only — anti-laundering
# keystone (other practices setting channel get 400 at ingress).
#
# Per-channel value-semantics is autonomy's Phase-2 work; v1 just
# carries the channel + exempts on receive.
TYPED_CONTROL_CHANNELS: frozenset[str] = frozenset(
    {
        "trust_tier_update",
        "posture_sync",
        "liveness_probe",
        "membrane_coord",
        "routing_verdict",  # auto_route | eco_escalate
        "eco_verdict",  # accept | change | decline | defer
        "guardian_action",  # unblock-the-unblocker / QUESTION_STATE_UNBLOCK
    }
)

# Practice-slug PATTERNS that are allowed to emit typed control-plane
# channels. Matched against the bare project slug (basename) regardless
# of tenant — control-plane is inherently cross-tenant guardian coord,
# so the gate is by practice-TYPE not by fixed tenant. Covers David's
# and Philipp's autonomy + mesh-support seats (slugs with or without
# the empirica- prefix per the org-empirica convention).
_CONTROL_PLANE_SLUG_PATTERNS: frozenset[str] = frozenset(
    {
        "autonomy",
        "empirica-autonomy",
        "mesh-support",
        "empirica-mesh-support",
    }
)


def is_control_plane_practice_slug(slug: str) -> bool:
    """True if the given project slug is an authorized control-plane
    practice (autonomy/mesh-support, with or without empirica- prefix).
    """
    return slug in _CONTROL_PLANE_SLUG_PATTERNS


# Moved with the contract from cortex.tenant: pure string logic over canonical ids, so it needs no tenant store.
def derive_mesh_layer(source_canonical: str, target_canonicals: list[str]) -> str:
    """Derive the routing layer for a proposal — single field, strictest
    across all targets (David, 2026-06-01: "we are not the internet,
    just an org and tenant scoped network running on top of vpns").

    Returns ``"L1"`` | ``"L2"`` | ``"L3"``:
      - **L1** — every target equals source (self / own subagents)
      - **L2** — at least one target shares the source's org but
        differs at tenant or project (same-org cross-practice)
      - **L3** — at least one target is in a different org

    Strictness ordering: L3 > L2 > L1. The single field tells consumers
    "this proposal at least touches an L3 boundary if any target is
    cross-org" — sufficient for render badges + the consumer-side
    enforcement check. Per-target stamps are intentionally NOT done for
    v0; render side can re-derive per-target from the canonical ids if
    granular badges become needed (extension prop_tml5k7a3 §1.2).

    Non-canonical (non-dot) ids degrade to L3 — strictest, safer default.
    """
    if not source_canonical or not target_canonicals:
        return "L3"  # missing info → strictest

    # Self-target-first check (extension prop_vfth2cilcz 2026-07-16):
    # when every target equals source_canonical the work is self-only —
    # that's L1 regardless of whether the id is in canonical 3-form or
    # legacy bare/2-level form. The in-loop `tgt == source → continue`
    # already treats self-targets as L1, but the canonical-parse guard
    # below short-circuits to L3 BEFORE the loop runs — so legacy
    # `cortex → cortex` self-tests + `empirica.david → empirica.david`
    # smoke rows got stamped L3. This pre-check restores L1 for the
    # self-target case without loosening the cross-target strictness.
    non_empty_targets = [t for t in target_canonicals if t]
    if non_empty_targets and all(t == source_canonical for t in non_empty_targets):
        return "L1"

    src_parts = source_canonical.split(".", 2)
    if len(src_parts) < 3:
        return "L3"
    src_org = src_parts[0]
    layer = "L1"
    for tgt in target_canonicals:
        if not tgt:
            continue
        if tgt == source_canonical:
            continue  # self → contributes L1 only
        tgt_parts = tgt.split(".", 2)
        if len(tgt_parts) < 3:
            return "L3"  # non-canonical target → strictest
        if tgt_parts[0] != src_org:
            return "L3"  # any cross-org target wins
        # Same org, different practice → at least L2 (continue loop in
        # case a later target raises to L3)
        if layer == "L1":
            layer = "L2"
    return layer


class ProposalType(str, Enum):
    """Proposal types the orchestration module routes.

    Starting set for MVP; more added as handlers are implemented (T2c+).
    """

    PUBLISH = "publish"
    SPEC_UPDATED = "spec_updated"
    ARCHITECTURE_DECISION = "architecture_decision"
    INVESTIGATION_REQUEST = "investigation_request"
    TRUST_ESCALATION_REQUEST = "trust_escalation_request"
    CODE_CHANGE_REQUEST = "code_change_request"
    COLLAB_BRIEF = "collab_brief"  # spawned by /v1/collab/doc/{id}/submit
    # Phase 4 — TL;DR-AI Pass 2. AI-inferred edge from a source artifact
    # to an existing artifact with a confidence in [0.55, 0.90).
    # Auto-accept (≥0.90) bypasses the proposal layer entirely; below
    # 0.55 dropped silently.
    EDGE_SUGGESTION = "edge_suggestion"
    # Claude-observed cortex bug (truncation, malformed result, unexpected
    # error). Mirrors a /v1/bug submission into the org admin's inbox so
    # bugs ride the same orchestration surface as peer asks (goal 9c861a50,
    # SER ser_cd095e #6 spirit — admin work-stream visibility unified).
    BUG_REPORT = "bug_report"
    SKILL_UPDATE = "skill_update"
    # Structured fleet-health report from a diagnostics suite (e.g.
    # nle-diagnostics). Routed cross-org via G5 to mesh-support; surfaced
    # in extension System tab as a per-seat health row with last-run
    # prominently displayed. Payload = the §3 envelope documented in
    # mesh-support's diagnostics-report-protocol.md: {suite, suite_version,
    # ran_by, ran_at, overall ∈ {pass,warn,fail}, checks[]}. Cortex stays
    # payload-agnostic (no Pydantic gate until ≥2 suites stabilize the
    # shape). David greenlit 2026-06-15 via prop_uywn7m3c6.
    DIAGNOSTICS_REPORT = "diagnostics_report"


# Type → surface mapping (extension prop_6eqmfj5x + empirica prop_xmqfpsn2 +
# extension prop_vssqsytc convergence, 2026-06-02; David-blessed for ship).
# Each surface lists the types that render there. Types can appear in multiple
# surfaces (e.g. trust_escalation_request is both eco-decidable and a
# governance signal). Consumers query /v1/orchestration/inbox?surface=<name>
# to get the filtered set.
#
# - eco: needs human decision (Accept/Change/Decline). Most typed proposals.
# - collab: noetic AI↔AI chat. Renders in extension Collab pane via /threads.
# - system: governance/cross-org attention (admin-decisions or system-tab notices).
# - publish: post-accept outreach dispatch (subset of eco lifecycle).
# - reports: the Shared Epistemic Record projection, retired (Cortex stage 2, 2026-10-24). Kept so the matrix
#   matches Cortex's; never sourced from the inbox.
TYPES_FOR_SURFACE: dict[str, set[str]] = {
    "eco": {
        ProposalType.ARCHITECTURE_DECISION.value,
        ProposalType.CODE_CHANGE_REQUEST.value,
        ProposalType.INVESTIGATION_REQUEST.value,
        ProposalType.PUBLISH.value,
        ProposalType.SPEC_UPDATED.value,
        ProposalType.TRUST_ESCALATION_REQUEST.value,
        ProposalType.EDGE_SUGGESTION.value,
        ProposalType.SKILL_UPDATE.value,
    },
    "collab": {
        ProposalType.COLLAB_BRIEF.value,
    },
    "system": {
        ProposalType.TRUST_ESCALATION_REQUEST.value,
        ProposalType.BUG_REPORT.value,
        ProposalType.DIAGNOSTICS_REPORT.value,
    },
    "publish": {
        ProposalType.PUBLISH.value,
    },
    # reports surface: the retired Shared Epistemic Record projection. Not derived from the proposal type, and the
    # inbox query has no filter for it. Empty, and listed only so the matrix matches Cortex's.
    "reports": set(),
}


def types_for_surface(surface: str) -> set[str] | None:
    """Return the type allowlist for the given surface name.

    Returns None on unknown surface (caller treats as 'no filter').
    Returns empty set for surfaces that aren't inbox-sourced (e.g. reports);
    caller should typically treat that as 'no matches' rather than 'no filter'.
    """
    if not surface:
        return None
    s = surface.lower().strip()
    return TYPES_FOR_SURFACE.get(s)


class ProposalStatus(str, Enum):
    """Lifecycle states for a Proposal.

    Naming aligns with PROPOSAL_ECO_ACTION_SEMANTICS — Accept/Change/Decline.
    The CHANGE_COUNT cap (MAX_CHANGE_REQUESTS) prevents runaway oscillation
    between ECO and source Claude.
    """

    ECO_REVIEW = "eco_review"  # awaiting ECO (human) decision
    AUTO_ROUTED = "auto_routed"  # trust-approved, dispatched
    ACCEPTED = "accepted"  # ECO accepted or auto-routed + handler ran
    DECLINED = "declined"  # ECO declined the proposal
    CHANGED = "changed"  # ECO requested change; type/content may have changed
    EXPIRED = "expired"  # TTL elapsed without decision
    ESCALATED = "escalated"  # change_count exceeded MAX_CHANGE_REQUESTS

    # Completion-ack lifecycle — target AI signals work landed. accepted →
    # completed (shipped) | wont_fix (decided not to deliver). FAILED (below)
    # is reused for both publish-dispatch-error and complete-result=failed.
    COMPLETED = "completed"  # target AI delivered the work (result=shipped)
    WONT_FIX = "wont_fix"  # target AI decided not to do it; closes loop

    # Publish-only lifecycle (per EXTENSION_PUBLISHING_GATEWAY_BRIEF). These
    # values appear only on type=publish proposals — orchestration types
    # (architecture decisions, spec updates, etc.) terminate at ACCEPTED.
    #
    # Two-stage flow (David, 2026-05-17):
    #   ECO_REVIEW → article-level decision (is this draft good?)
    #     Accept → TARGETS_PENDING  (now: which platforms?)
    #     Change → ECO_REVIEW       (back to source AI for redraft, global)
    #     Decline → DECLINED        (terminal)
    #   TARGETS_PENDING → per-platform decisions on platform_decisions[k]
    #     All decided + ≥1 accepted → ACCEPTED_PENDING_DISPATCH
    #     All declined → DECLINED
    #   ACCEPTED_PENDING_DISPATCH → outreach polls outbox, dispatches the
    #     accepted-platform variants only
    #     Outreach completes → PUBLISHED | FAILED
    TARGETS_PENDING = "targets_pending"  # article approved, awaiting per-platform selection
    ACCEPTED_PENDING_DISPATCH = "accepted_pending_dispatch"  # outreach picks up dispatch
    PUBLISHED = "published"  # dispatcher succeeded
    FAILED = "failed"  # dispatcher errored — extension shows in unpublished filter
    # ALSO used for orchestration complete(result=failed)


# States in which the RECEIVER is legitimately doing the work, so a
# completion ack must be expressible. The ONE definition — the REST
# handler and the MCP tool both import it, because they drifted once
# already (REST rejected accepted_pending_dispatch while MCP took it,
# 2026-07-17) and a hand-mirrored pair re-drifts.
#
# CHANGED is here on purpose (2026-08-24, core's report prop_ntmfpfvr):
# the reaction protocol tells a receiver holding a `changed` proposal to
# proceed with the ADJUSTED scope — so the receiver ends up having done
# real work with no way to close the loop, and the sender's outbox
# dangles forever. The old predicate asked "is the status accepted NOW"
# where the contract question is "has ECO routed this to the receiver
# for action" — one predicate answering two questions, which agree
# everywhere except iterated proposals. The completion note records what
# actually shipped against the adjusted scope.
COMPLETABLE_STATUSES = frozenset(
    {
        ProposalStatus.ACCEPTED.value,
        ProposalStatus.ACCEPTED_PENDING_DISPATCH.value,
        ProposalStatus.CHANGED.value,
    }
)


# completion ``result`` -> (terminal status, audit action): the ONE map, used by REST and MCP.
RESULT_TO_STATUS = {
    "shipped": (ProposalStatus.COMPLETED.value, "completed"),
    "failed": (ProposalStatus.FAILED.value, "failed"),
    "wont_fix": (ProposalStatus.WONT_FIX.value, "wont_fix"),
}


ALL_STATUSES = frozenset(s.value for s in ProposalStatus)
ALL_SURFACES = frozenset(TYPES_FOR_SURFACE)
ALL_TYPES = frozenset(t.value for t in ProposalType)


def validate_vocabulary(
    param: str,
    raw: str | None,
    allowed: frozenset,
) -> str | None:
    """Return an error naming ``raw`` and ``allowed``, or None if acceptable.

    Absent/empty is always acceptable — it means "no filter". An UNKNOWN value
    never is.

    THE ENUMERATION THIS CAME FROM. After fixing ``status``, I logged an
    assumption at 0.5 that ``status`` and ``origin`` were the only constrained
    filters on these handlers, admitting I had not checked. Enumerating the 16
    query params refuted it in three places, each failing DIFFERENTLY:

    - ``surface``: ``types_for_surface`` returns None on an unknown name and
      the caller reads None as "no filter", so ``surface=eeco`` returned the
      UNFILTERED inbox **wearing the filter's name** — the exact sentence the
      ``origin`` regression test was written against, live one param over.
    - ``type``: pushed into the Qdrant match, so a typo returned a confident
      zero. The ``status`` shape.
    - ``scope``/``endpoint``: compared against literals, so a typo silently
      degrades to a different scope or an empty page.

    Three filters, three failure modes, one cause: **nobody enumerated the
    filters and asked which validate.** A correct principle applied to some of
    a set is a coincidence, not a principle.

    Checked the live values first this time (the nginx access log holds every
    one in flight) because the ``status`` fix broke a consumer that had been
    sending an invalid value for months. All current traffic is valid.
    """
    if not raw or not str(raw).strip():
        return None
    value = str(raw).strip()
    if value in allowed:
        return None
    return f"unknown {param} value: {value} — valid: {', '.join(sorted(allowed))}"


def parse_status_filter(raw: str | None) -> tuple[list[str], str | None]:
    """Split a comma-separated ``status`` filter, REFUSING unknown values.

    Returns ``(values, error)``. ``error`` is None on success; on failure it
    names the rejected values and the full valid set, and the caller must 400
    rather than query.

    WHY REFUSE RATHER THAN IGNORE. An unrecognised status previously fell
    straight through to the Qdrant match and returned ``returned=0,
    matched=0`` — a confident, complete-looking zero INDISTINGUISHABLE from an
    honest empty result. Measured on prod 2026-09-16: ``status=compleeted``
    and ``status=targets_pendng`` each answered exactly as a real-but-empty
    query does, while ``status=completed`` returned matched=22. A caller reads
    that as "no such work exists" and stops looking.

    That is what made every client's status vocabulary load-bearing, and it is
    why the CLI's 7-of-13 drift survived months: the one surface that could
    have contradicted the client agreed with it instead. Ask for a status your
    client forgot and the server says zero, exactly as if none existed.

    Same defect and same remedy as the CRM lane's unknown-``scope`` filter
    (shipped c05b2f52) — refuse, naming the valid values.

    An absent or empty filter is not an error; it means no filter.
    """
    if raw is None:
        return [], None
    values = [s.strip() for s in str(raw).split(",") if s.strip()]
    if not values:
        return [], None
    unknown = [v for v in values if v not in ALL_STATUSES]
    if unknown:
        return [], (f"unknown status value(s): {', '.join(unknown)} — valid: {', '.join(sorted(ALL_STATUSES))}")
    return values, None


# Statuses from which NO further work is expected — the proposal's story is
# over. Used by the expiry post-filter to decide what a past-TTL row means.
#
# THE SET IS A DENYLIST ON PURPOSE (outreach's option (c), 2026-08; narrowed
# scope agreed with outreach 2026-09-16). An ALLOWLIST of "keep these" has to
# be updated every time a status is added, and a status it forgets is dropped
# in silence — which is the bug class already in play here. Inverted, a new
# status defaults to RETAINED: visible and arguably noisy, rather than gone
# and undetectable. Noisy is a bug report; gone is not.
TERMINAL_STATUSES = frozenset(
    {
        ProposalStatus.COMPLETED.value,
        ProposalStatus.DECLINED.value,
        ProposalStatus.WONT_FIX.value,
        ProposalStatus.PUBLISHED.value,
        ProposalStatus.FAILED.value,
        ProposalStatus.EXPIRED.value,
        ProposalStatus.ESCALATED.value,
    }
)


def survives_expiry(proposal: Any, now_iso: str) -> bool:
    """Should a row still appear on a read surface, given its TTL?

    Not-yet-expired rows always survive. A PAST-TTL row survives only when
    it is a ``publish`` row at a non-terminal status — i.e. work the publish
    lifecycle still owes someone.

    WHY PUBLISH ONLY, for now. The expiry filter hides 72% of the store
    (7,779 of 10,751 rows measured 2026-09-16), and 5,605 of those are
    non-terminal — 5,553 of them ``accepted``. Restoring all of them at once
    is not a surface change, it is an unannounced gardening event delivered
    to four practices simultaneously with no triage affordance (outreach's
    framing, prop_qdp73yfn, and they are right): an unusable surface and a
    hidden one fail differently and fail equally.

    NARROWED TO PUBLISH, THIS RESTORES **TWO** LIVE ROWS. Nineteen match the
    predicate; 17 of those are globally archived and were never going to
    surface, because the archive exclusion runs independently of this one.
    The 19 was the figure I first measured and reported, and counting
    archived rows as restorable is the same predicate-over-the-wrong-
    population error this filter is an instance of.

    The two are ``prop_vehq3pwv`` and ``prop_numuat6uune``, both pinned at
    ``targets_pending`` by one undecided platform — which the dispatcher
    could never match and expiry then hid. That is the OKF stranding path,
    closed. Two is the right number and it is the whole casualty.

    The general restore stays open, and it needs a default date window, a
    bulk-triage path, and notice — not a wider predicate. Widening here is
    ONE condition; do not widen it without those three.

    AND ONE EXPOSURE THAT IS NOT ABOUT PANES (autonomy, prop_s5zketek).
    Their gate does not RENDER restored rows, it EMITS on them — poll_inbox
    carries no age filter — so a wide restore would issue fresh verdicts to
    peers about months-old proposals. A reader can ignore a noisy pane; a
    peer cannot un-receive a verdict. Any future widening has to answer the
    emitters, not only the renderers.
    """
    expires_at = getattr(proposal, "expires_at", None)
    if not expires_at or expires_at >= now_iso:
        return True
    if getattr(proposal, "type", None) != "publish":
        return False
    status = getattr(proposal, "status", None)
    if status in _EXPIRY_HELD_BACK_STATUSES:
        return False
    return status not in TERMINAL_STATUSES


# The ONE non-terminal status deliberately held back, and it is a safety
# constraint rather than a scope preference (autonomy, prop_s5zketek, relayed
# in outreach's consolidated answer prop_rr75nydu). ``accepted`` is the only
# status autonomy's proposal_processor.poll_inbox selects, and on
# --emit-verdicts it REPLIES to the source AI. A restored ``accepted`` row is
# therefore not a row someone might see — it is a verdict someone receives, on
# an ask they may have closed months ago, arriving as current.
#
# There are ZERO past-TTL publish rows at ``accepted`` today, so this changes
# nothing now and is entirely about the row that shows up later. Remove it
# when autonomy's age guard exists; until then it is the difference between a
# display failure that stays inside a practice and an emit failure that leaves
# the box.
_EXPIRY_HELD_BACK_STATUSES = frozenset({ProposalStatus.ACCEPTED.value})


# Hard cap on change-request cycles before a proposal escalates. Prevents
# runaway oscillation between ECO and source Claude.
#
# The MAX_RECLASSIFY alias is GONE (2026-08-02). It was introduced "for
# in-flight imports" and carried a `# deprecated` marker that aged past 90
# days with all 8 call sites still on the old name — the codify-or-close
# window had passed and nobody closed it, which is what a deprecation
# marker is supposed to prevent rather than record. Call sites migrated.
MAX_CHANGE_REQUESTS = 3


class ActionCategory(str, Enum):
    """Action risk category used by the trust bridge to gate routing."""

    REFLEX = "REFLEX"  # safe, automatic
    OPERATIONAL = "OPERATIONAL"
    TACTICAL = "TACTICAL"  # MVP default
    STRATEGIC = "STRATEGIC"
    IRREVERSIBLE = "IRREVERSIBLE"


# Every audit action that RECORDS a decision, in every lane. This set answers
# WHO decided (the actor prefix carries human-vs-system); it is not a verdict
# on WHAT was decided, which is why declines and partial declines belong here
# as much as accepts.
#
# The publish lane decides through its own vocabulary — per-platform, per-
# tenant and per-article — and was missing from this set, so a publish a human
# approved read `system` (finding a99868a9: three articles on David's public
# timeline were escalated as ungated on that reading). Each of these is
# written only by an ECO decision endpoint with the caller's actor; every
# automated decider signs `system:*`, which the walk below still maps to
# `system`. tests/test_orchestration.py classifies every action the decision
# endpoints write, so a new one fails CI until someone decides which it is.
_PUBLISH_DECISION_ACTIONS = frozenset(
    {
        "accept_pending_dispatch",  # publish accepted, awaiting dispatch
        "platform_decided",  # accept/decline of one platform target
        "tenant_decided",  # one tenant's slot on a multi-tenant row
        "tenant_partial_accept",  # a tenant accepted; others still pending
        "tenant_partial_decline",  # a tenant declined; others still pending
        "article_accepted",  # the article itself, before per-platform
    }
)
_DECISION_ACTIONS = frozenset({"accepted", "declined", "changed"}) | _PUBLISH_DECISION_ACTIONS

# A publish row is decided per PART — the article, then each platform, then
# each tenant — and a part-decision leaves the row still awaiting the rest.
# In these statuses a publish-lane entry says a human has started deciding,
# not that the row IS decided; answering `human` there moved rows with
# platforms still pending out of any "awaiting decision" bucket (measured on
# prod 2026-09-25: 3 targets_pending rows).
_AWAITING_DECISION_STATUSES = frozenset({"eco_review", "targets_pending"})

# Statuses a row can only reach by LEAVING the gate. A row sitting in
# eco_review is legitimately undecided; a row that is terminal with no
# decision in its audit trail reached that state without a human — which
# is a system disposition, not a missing one.
_POST_GATE_STATUSES = frozenset(
    {
        "accepted",
        "declined",
        "changed",
        "auto_routed",
        "completed",
        "wont_fix",
        "expired",
        "escalated",
        "failed",
    }
)


def decided_by_kind(p: Any) -> str | None:
    """Who decided this proposal: 'human' | 'system' | None (undecided).

    THE one definition. It lives HERE, beside the model, so that
    ``Proposal.to_wire`` can call it — every surface that serializes a
    proposal then carries the field, instead of only the handlers that
    remember to stamp it. ``proposal_store`` re-exports it, so importers
    of the old location keep working.

    Walks audit_log newest-first for the last decision action; a
    ``system:`` actor means the row never transited the ECO gate
    (system:collab-bypass et al.).

    Terminal-without-a-decision reads ``system``, not None (extension
    prop_afo7tqi2, 2026-09-11): auto_routed rows carry an empty
    eco_decision and an audit trail ending at the emitting practice, so
    the walk found nothing and returned None — 13 of David's 19 visible
    "decisions" were rows no human ever saw, kept only because the
    filter excluded ``system`` and NULL is not ``system``. A decided row
    handing a consumer a NULL kind is the store failing to answer its
    own question.

    MOVED HERE from proposal_store 2026-09-18. It had exactly one wire
    emission site — the INBOX LIST handler — so `mailbox poll` carried
    the field and `mailbox show` did not. Two practices measured the two
    surfaces, got opposite answers, and both were right: autonomy read
    130/130 present on poll, mesh-support 0/3 on show. A field computed
    in the store and stamped by one handler is a projection that omits
    what storage holds; computing it in `to_wire` is what makes the two
    surfaces agree by construction rather than by discipline.
    """
    for entry in reversed(getattr(p, "audit_log", None) or []):
        if isinstance(entry, dict) and entry.get("action") in _DECISION_ACTIONS:
            if (
                entry.get("action") in _PUBLISH_DECISION_ACTIONS
                and str(getattr(p, "status", "")) in _AWAITING_DECISION_STATUSES
            ):
                return None
            actor = str(entry.get("actor") or "")
            return "system" if actor.startswith("system:") else "human"
    if getattr(p, "auto_routed", False) or str(getattr(p, "status", "")) in _POST_GATE_STATUSES:
        return "system"
    return None


class TrustMode(str, Enum):
    """Trust mode returned by the Autonomy trust bridge (or stubbed locally)."""

    CONTROLLER = "CONTROLLER"  # always ECO-gate
    SUPERVISOR = "SUPERVISOR"
    PARTNER = "PARTNER"
    AUTONOMOUS = "AUTONOMOUS"


@dataclass
class TrustLevel:
    """Trust decision from the Autonomy bridge (or T2a stub).

    score: 0.0..1.0, how much the source Claude is trusted for this action.
    level: qualitative label.
    mode: how routing should treat it (CONTROLLER → always ECO-gate).
    """

    score: float
    level: str
    mode: str


def _new_proposal_id() -> str:
    """Generate a proposal id. Format: prop_<22-char-base32-uuid>."""
    import base64

    return "prop_" + base64.b32encode(uuid.uuid4().bytes).decode("ascii").rstrip("=").lower()


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_plus_hours(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat().replace("+00:00", "Z")


@dataclass
class Proposal:
    """The generic Proposal envelope.

    Stored in the orchestration_proposals Qdrant collection, one point per
    proposal, with vector embedded from (title + summary + claudes + type).
    """

    # Identity
    id: str = field(default_factory=_new_proposal_id)
    type: str = ProposalType.CODE_CHANGE_REQUEST.value
    parent_id: str | None = None
    thread_root_id: str | None = None

    # Addressing
    source_claude: str = ""
    source_project: str = ""
    target_claudes: list[str] = field(default_factory=list)
    target_projects: list[str] = field(default_factory=list)

    # Identity-level disambiguation (Phase 2 ai_id, 2026-05-31). Resolved
    # at /propose time from source api_key + tenant_db.find_users_for_ai_id.
    # Lets inbox/outbox filter by user identity rather than (org_id, ai_id)
    # alone — disambiguates David vs Philipp within org-empirica who both
    # have ai_id='cortex'. Legacy proposals have empty values and fall
    # back to the target_claudes match.
    source_user_id: str = ""
    target_user_ids: list[str] = field(default_factory=list)
    # Distinct org_ids of the RESOLVED target users, stamped at /propose
    # time (router) via the same find_users_for_ai_id resolution the ntfy
    # fan-out uses. Read-side purpose: a cross-org proposal is stamped
    # org_id = SOURCE org, so the RECEIVING org's eco_view could never
    # see the row it is supposed to decide (David 2026-08-29: cross-org
    # collabs gate at the receiving ECO only — which makes receiving-org
    # visibility load-bearing, 12-day-park class). Empty on legacy rows.
    target_org_ids: list[str] = field(default_factory=list)

    # Mesh routing layer (MESH_ROUTING_PROTOCOL §1.2). Computed once at
    # /propose time via tenant.derive_mesh_layer; stamped here so render
    # consumers don't re-derive (re-derivation drift was the proximate
    # cause of the 2026-06-01 cross-tenant leak — extension prop_tml5k7a3).
    # "L1" | "L2" | "L3", strictest across all targets.
    layer: str = "L3"  # default strictest if not computed

    # Display
    title: str = ""
    summary: str = ""

    # Type-specific payload (JSON-serializable)
    payload: dict[str, Any] = field(default_factory=dict)
    media_refs: list[dict[str, Any]] = field(default_factory=list)

    # Mutable draft text — populated only when type=publish. The publish
    # surface IS the editing surface (per EXTENSION_PUBLISHING_GATEWAY_BRIEF):
    # users edit the draft inline, the new text replaces this field, and
    # /respond action=accept ships whatever's currently in mutable_draft.
    # Frozen on accept (becomes the immutable snapshot Zernio dispatched).
    # Other proposal types leave this None — their envelope is immutable
    # by design.
    mutable_draft: str | None = None

    # Source collab doc id — populated only when type=collab_brief. Set when
    # /v1/collab/doc/{id}/submit spawns this proposal. ECO _apply_change /
    # _apply_accept / _apply_decline use this to find the source doc and
    # update its status (auto-reopen on change, archive on accept/decline).
    # Other proposal types leave this None.
    source_collab_doc_id: str | None = None

    # Timing
    created_at: str = field(default_factory=_iso_now)
    # Last write to this row, stamped by ProposalStore.create/update (the only write points). None on rows last written
    # before stamping began (updated_since.RECORDED_SINCE): absence means "not recorded", never "not modified".
    updated_at: str | None = None
    # 30d default — eco_review proposals sit in inbox until acted on; short
    # TTLs caused user-visible "Link expired" without protecting anything
    # (idempotency + status checks gate misuse, not token expiry).
    expires_at: str = field(default_factory=lambda: _iso_plus_hours(24 * 30))

    # State
    status: str = ProposalStatus.ECO_REVIEW.value

    # Trust + routing
    trust_required: str = ActionCategory.TACTICAL.value
    action_category: str = ActionCategory.TACTICAL.value
    auto_routed: bool = False

    # ECO response (when status transitions via ECO)
    eco_decision: dict[str, Any] | None = None

    # Append-only audit trail (list of dicts)
    audit_log: list[dict[str, Any]] = field(default_factory=list)

    # Tenant scope (set by store at insert time)
    org_id: str = ""

    # Oscillation guard: bounded reclassify cycles. Escalates at MAX_RECLASSIFY.
    reclassify_count: int = 0

    # Soft-delete: ECO can archive any proposal at any state to clear noise
    # from active surfaces. Orthogonal to status (accept/change/decline are
    # decision-carrying terminals; archive is pure cleanup that doesn't
    # signal anything to the source AI). Default queries exclude
    # is_archived=true. Reversible: archive(unarchive=True) flips it back.
    is_archived: bool = False
    archived_at: str | None = None
    archive_reason: str | None = None
    # PER-RECIPIENT archive (2026-08-02). is_archived above is one shared
    # boolean, so on a multi-target thread ONE recipient archiving cleared
    # it from every co-recipient's mailbox — 1670 multi-target proposals
    # live, 202 of them open. archived_by holds the canonical practice ids
    # that dismissed their own copy; read filters exclude rows containing
    # the CALLING practice. is_archived keeps its meaning (globally retired:
    # the supersede cascade, plus 3982 legacy rows) — the two are different
    # acts, not two ways to do one thing. See orchestration/archive_scope.py.
    archived_by: list[str] = field(default_factory=list)

    # Completion-ack — target AI signals work landed (shipped / failed /
    # wont_fix). Closes the loop without human mediation. status transitions
    # accepted → completed (or failed / wont_fix). Source AI sees via
    # cortex_outbox_poll(status="completed").
    completed_at: str | None = None
    completion_note: str | None = None  # 1-3 sentence summary of delivery
    completion_commit_sha: str | None = None  # optional git ref tying work to commit
    completion_artifact_ids: list[str] = field(default_factory=list)
    completion_result: str | None = None  # "shipped" (default) | "failed" | "wont_fix"
    # Per-target acknowledgements for multi-target proposals: {target ai_id: {result, note, commit_sha,
    # artifact_ids, actor, at}}. Empty for single-target proposals. The proposal turns terminal only when
    # every target in target_claudes has an entry (cortex.orchestration.target_acks).
    target_acks: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Push-event tracking (per prop_efsdt3vidjdevmkngw2wvw6fhe — close the
    # push gap). last_ntfy_emit_at records when a state-transition event was
    # last published to ntfy. Zero-cost when push events disabled — field
    # stays None. The reminder/escalation chain that previously kept
    # reminder_emitted state was retired 2026-06-21 (prop_iazbb2rb,
    # mailbox-first model); push_events.py from_dict tolerates the legacy
    # key in older payloads.
    last_ntfy_emit_at: str | None = None

    # Per-platform decisions for publish proposals (David, 2026-05-17):
    # accept/decline per platform variant. `change` stays global (it's
    # about the article itself, not the destination). Outreach polls
    # outbox(accepted_pending_dispatch) and dispatches only platforms
    # whose decision is "accepted" — declined platforms stay un-shipped.
    # Shape: {platform_name: {decision: "accepted"|"declined", actor, at, note?}}
    # Empty dict on non-publish proposals or until first per-platform action.
    platform_decisions: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Per-tenant ECO decisions on multi-tenant proposals (David, 2026-07-08
    # via empirica prop_5mrws4tv). A cross-tenant proposal must NOT be
    # bound by a single foreign-tenant ECO — each target tenant's ECO
    # gates its own participation independently. Aggregate status stays
    # eco_review until ALL expected tenants decide; downstream binding
    # (shared-record participants) restricts to accepted-tenant targets.
    #
    # Shape: {tenant_slug: {decision: "accepted"|"declined", actor,
    #         at, note?}}
    # Single-tenant proposals (len(expected_tenants) == 1) don't use this
    # — current behavior unchanged. Legacy proposals in-flight default to
    # empty dict; derive_multi_tenant_aggregate_status detects this case
    # and short-circuits to the single-tenant fast path so back-compat
    # holds during the rollout window.
    tenant_decisions: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Shared Epistemic Record fields, retired with SERs (Cortex stage 2, 2026-10-24). Kept on the envelope because older
    # rows and wake events still carry them; core never sets them, and both are None on anything written since.
    ser_id: str | None = None
    ser_state_verified: bool | None = None

    # Autonomy triage decision — ambassador lane 3 (2026-06-02). Set by
    # the router when ORCHESTRATION_AUTONOMY_TRIAGE_URL is configured and
    # the per-proposal triage_proposal() call succeeds. autonomy_confidence
    # ∈ [0,1] is the calibration of the decision; autonomy_rationale is a
    # human-readable explanation. Both ride the proposal envelope (audit
    # trail) + wake event payload. Extension surfaces them on Inbox
    # Decided|Auto tabs so the human ECO can see "why" the decision happened.
    autonomy_confidence: float | None = None
    autonomy_rationale: str | None = None
    # autonomy_verdict_basis — structured reason for the decision (autonomy
    # commit 4d70589, 2026-06-03 via prop_a3wkr6dwn). Renderable per-card
    # badge: auto_routed | category_block | cross_boundary | confidence_floor
    # | routing_held | anomaly(reserved-future). Set alongside _confidence
    # and _rationale; rides envelope + wake event payload + audit. Extension
    # renders as a labelled badge so the marketing claim ("we surface
    # critical / irreversible / not-yet-calibrated decisions") shows on
    # every escalated card as concrete evidence rather than only website copy.
    autonomy_verdict_basis: str | None = None

    # Typed control-plane channel (prop_smjvlyt4 formalized 2026-06-26).
    # Nullable; None = free-form collab (current default). When set, value
    # MUST be in TYPED_CONTROL_CHANNELS and the source_practice slug MUST
    # be autonomy/mesh-support (validated at ingress in from_request).
    # Channel-scoped exemption from posture mute (collab_posture.py:133)
    # — control-plane unmutable by design per DoR §6. Single audit-able
    # exemption point. Composes with autonomy's Phase-2 /v1/triage-for-
    # collab attenuation; this v1 carries the channel field + source-gate
    # so the gated-default flip doesn't silently mute guardian
    # coordination.
    channel: str | None = None

    @staticmethod
    def resolve_thread_root_id(store: Any, parent_id: str | None) -> str | None:
        """Walk one hop up to find the TRUE thread root of a reply chain.

        Ecodex prop_qb7qnxqrt (2026-07-07): before this helper, replies
        anchored their thread_root_id at their DIRECT parent, so a chain
        A→B→C→D produced thread_roots [null, A, B, C] — no proposal
        shared a thread_root with any non-adjacent hop, splintering any
        thread-grouped view into per-hop fragments.

        Algorithm:
        - No parent → None (this IS a thread root)
        - Parent has no thread_root (was a root itself) → parent_id
          (this is a depth-1 reply; parent IS the root)
        - Parent has thread_root → parent.thread_root_id (inherit for
          depth-≥2 replies)
        - Parent lookup fails (missing / store error) → parent_id
          (safe fallback; doesn't propagate the bug forward)

        The helper is store-typed as Any to avoid a schema→store import
        cycle. Callers pass the ProposalStore instance directly.
        """
        if not parent_id:
            return None
        try:
            parent = store.get(parent_id)
        except Exception:
            return parent_id
        if not parent:
            return parent_id
        return getattr(parent, "thread_root_id", None) or parent_id

    @classmethod
    def from_request(
        cls,
        body: dict[str, Any],
        *,
        org_id: str,
        source_fallback: str = "",
        canonicalize: Callable[[str], str] | None = None,
    ) -> Proposal:
        """Build a Proposal from a /propose request body.

        Applies defaults, derives thread_root_id from parent_id if needed.

        Strict-canonical egress (David 2026-06-03 rip): when ``canonicalize``
        is provided, source_claude + every target_claude is canonicalized
        to the canonical ``<org>.<tenant>.<project>`` mesh form here at
        envelope construction. Single canonicalization codepath — MCP +
        REST + any future caller share it. Non-resolvable ids pass through
        unchanged; router-side bounce-back catches them. canonicalize absent
        (e.g. unit tests using placeholder ids) → no canonicalization,
        router still bounces unresolvable ids.
        """
        # NOTE — type is deliberately NOT validated here. `Proposal.validate()`
        # already rejects unknown types, and every caller runs it: the MCP path
        # via `orchestration_tool` (returns an error envelope) and the REST path
        # via `endpoints`/`endpoints_trust` (returns 400). Raising here instead
        # would duplicate that check AND break the documented 400 contract by
        # turning a clean validation error into an exception.
        #
        # Recorded because I added exactly that guard on 2026-08-12 after
        # calling `from_request` in isolation, watching it return
        # `type='skill_update'`, and concluding the system accepted unknown
        # types. It does not — the construction step is not the validation
        # step, and testing a constructor without the validator its callers run
        # measures a pipeline that nobody executes.
        raw_type = body.get("type", ProposalType.CODE_CHANGE_REQUEST.value)

        expires_hours = int(body.get("expires_hours", 24 * 30))
        # `or source_fallback` — see seatless_source_fallback below for what
        # that fallback must be. Canonicalization CANNOT rescue a bad one:
        # a user_name is not a practice slug, so it passes through unchanged.
        source_claude = body.get("source_claude") or source_fallback
        parent_id = body.get("parent_id")
        target_claudes = list(body.get("target_claudes") or [])

        # In core the caller supplies the lookup: Cortex binds it to its tenant database, a local server to its registry.
        # Core itself names no identity store (the contract moved from Cortex on David's ruling of 2026-11-06).
        if canonicalize is not None:
            source_claude = canonicalize(source_claude)
            target_claudes = [canonicalize(t) for t in target_claudes]

        prop = cls(
            type=raw_type,
            parent_id=parent_id,
            thread_root_id=body.get("thread_root_id") or parent_id,
            source_claude=source_claude,
            source_project=body.get("source_project", ""),
            target_claudes=target_claudes,
            target_projects=list(body.get("target_projects") or []),
            title=body.get("title", ""),
            summary=body.get("summary", ""),
            payload=dict(body.get("payload") or {}),
            media_refs=list(body.get("media_items") or body.get("media_refs") or []),
            # Envelope-level mutable_draft (publish proposals carry the
            # editable article body here so the extension card can render
            # the full draft instead of falling back to summary/tldr).
            # to_payload / from_payload already round-trip it; this is the
            # only place the field was getting dropped on the write path.
            mutable_draft=body.get("mutable_draft"),
            expires_at=_iso_plus_hours(expires_hours),
            trust_required=body.get("trust_required", ActionCategory.TACTICAL.value),
            action_category=body.get("action_category", ActionCategory.TACTICAL.value),
            org_id=org_id,
            channel=body.get("channel"),  # prop_smjvlyt4; validated below
        )
        # Publish flow inversion (David 2026-07-11 via outreach prop_dxctv376
        # + extension prop_ayw2jye design collab): publish proposals skip
        # the article-level Accept gate entirely — article-review moved to
        # chat where it's cheap, the pane becomes per-platform final gate
        # only. Emit directly at TARGETS_PENDING so per-platform accept /
        # change / decline is available on first render, no article-accept
        # intermediate. The old Stage-1 _apply_accept publish branch stays
        # as legacy defense for any lingering eco_review publishes (proposal
        # sweeper drains them naturally). from_dict hydration path is
        # unaffected — stored status wins there.
        if prop.type == ProposalType.PUBLISH.value:
            prop.status = ProposalStatus.TARGETS_PENDING.value
        # Layer derivation (MESH_ROUTING_PROTOCOL §1.2) — compute here so
        # ALL propose entry points get it. Caller-provided value takes
        # precedence for back-compat / explicit override, otherwise derive
        # from the (now canonicalized) source + targets.
        provided = body.get("layer")
        if provided:
            prop.layer = provided
        else:
            prop.layer = derive_mesh_layer(prop.source_claude, prop.target_claudes)
        return prop

    def distinct_targets(self) -> list[str]:
        """``target_claudes`` without repeats, in order."""
        return list(dict.fromkeys(self.target_claudes or []))

    def is_multi_target(self) -> bool:
        return len(self.distinct_targets()) > 1

    def owed_targets(self) -> list[str]:
        """Targets that have not acknowledged yet: the LOGIC rule. A proposal turns terminal when this is
        empty. Always empty for a single-target proposal, where the one completion IS the acknowledgement."""
        if not self.is_multi_target():
            return []
        return [t for t in self.distinct_targets() if t not in (self.target_acks or {})]

    def pending_targets(self) -> list[str]:
        """What an outbox view shows as outstanding. Never use it to decide anything; use ``owed_targets``.
        Empty once the proposal is terminal, including rows completed before per-target acks existed (they have
        no ``target_acks``, and listing every target as outstanding on closed work would be false). Collab briefs
        are NOT exempt: they now close on the last ack too, and a reader asking who is outstanding gets the answer."""
        if self.completed_at:
            return []
        return self.owed_targets()

    def validate(self) -> str | None:
        """Return an error message if invalid, else None."""
        if not self.title or not self.title.strip():
            return "title is required"
        # A credential in a proposal is stored, indexed and read back into other sessions'
        # context for as long as the row exists. Refuse loudly; the author can resend without it
        # (hand credentials over out-of-band, never through the mesh).
        import json as _json

        from empirica.core.orchestration.leak_guard import first_secret

        hit = first_secret(
            self.title,
            self.summary,
            _json.dumps(self.payload, default=str) if self.payload else "",
        )
        if hit is not None:
            where = ("title", "summary", "payload")[hit[0]]
            return (
                f"the {where} contains what looks like a credential ({hit[1]}). A proposal is "
                "stored, searchable and read back into other sessions, so credentials never "
                "ride in one: remove it and hand the credential over out of band."
            )
        if not self.source_claude:
            return "source_claude is required"
        if not self.target_claudes:
            return "target_claudes must not be empty"
        valid_types = {t.value for t in ProposalType}
        if self.type not in valid_types:
            return f"type must be one of {sorted(valid_types)}, got {self.type!r}"

        # Typed control-plane channel validation (prop_smjvlyt4 2026-06-26).
        # Two gates: (1) value must be in the locked enum,
        # (2) source practice slug must be control-plane (anti-laundering).
        if self.channel is not None:
            if self.channel not in TYPED_CONTROL_CHANNELS:
                return f"channel must be one of {sorted(TYPED_CONTROL_CHANNELS)}, got {self.channel!r}"
            # source_claude is canonical 3-form <org>.<tenant>.<project_slug>
            # by the time validate() runs. Parse to get the practice slug.
            parts = (self.source_claude or "").split(".", 2)
            if len(parts) != 3:
                return (
                    f"channel-set proposals require canonical source_claude "
                    f"(<org>.<tenant>.<project>); got {self.source_claude!r}"
                )
            source_slug = parts[2]
            if not is_control_plane_practice_slug(source_slug):
                return (
                    f"channel={self.channel!r} requires source practice "
                    f"slug in {sorted(_CONTROL_PLANE_SLUG_PATTERNS)}, "
                    f"got source_slug={source_slug!r}"
                )
            # collab_brief is the only type that carries channel (free-form
            # noetic). Typed praxic proposals (publish, code_change_request,
            # etc.) shouldn't ride this exemption — they have ECO + their
            # own channels.
            if self.type != ProposalType.COLLAB_BRIEF.value:
                return f"channel is only valid on type=collab_brief, got type={self.type!r}"
        return None

    def embedding_input(self) -> str:
        """Text embedded into the Qdrant vector for semantic inbox search."""
        targets = ",".join(self.target_claudes)
        return f"{self.title} {self.summary} {self.source_claude}→{targets} type={self.type}"

    def to_payload(self) -> dict[str, Any]:
        """Flat dict stored as the Qdrant point payload."""
        return {
            "id": self.id,
            "type": self.type,
            "parent_id": self.parent_id,
            "thread_root_id": self.thread_root_id,
            "source_claude": self.source_claude,
            "source_project": self.source_project,
            "target_claudes": self.target_claudes,
            "target_projects": self.target_projects,
            "title": self.title,
            "summary": self.summary,
            "payload_json": _json_dumps(self.payload),
            "media_refs": self.media_refs,
            "mutable_draft": self.mutable_draft,
            "source_collab_doc_id": self.source_collab_doc_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "trust_required": self.trust_required,
            "action_category": self.action_category,
            "auto_routed": self.auto_routed,
            "eco_decision_json": _json_dumps(self.eco_decision) if self.eco_decision else None,
            "audit_log_json": _json_dumps(self.audit_log),
            "org_id": self.org_id,
            "source_user_id": self.source_user_id,
            "target_user_ids": self.target_user_ids,
            "target_org_ids": list(self.target_org_ids or []),
            "layer": self.layer,
            "reclassify_count": self.reclassify_count,
            "is_archived": self.is_archived,
            "archived_at": self.archived_at,
            "archive_reason": self.archive_reason,
            "archived_by": list(self.archived_by or []),
            "completed_at": self.completed_at,
            "completion_note": self.completion_note,
            "completion_commit_sha": self.completion_commit_sha,
            "completion_artifact_ids": self.completion_artifact_ids,
            "completion_result": self.completion_result,
            "target_acks_json": _json_dumps(self.target_acks),
            "last_ntfy_emit_at": self.last_ntfy_emit_at,
            "platform_decisions_json": _json_dumps(self.platform_decisions),
            "tenant_decisions_json": _json_dumps(self.tenant_decisions),
            "ser_id": self.ser_id,
            "ser_state_verified": self.ser_state_verified,
            "autonomy_confidence": self.autonomy_confidence,
            "autonomy_rationale": self.autonomy_rationale,
            "autonomy_verdict_basis": self.autonomy_verdict_basis,
            "channel": self.channel,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Proposal:
        """Rehydrate from a Qdrant point payload."""
        return cls(
            id=payload["id"],
            type=payload.get("type", ProposalType.CODE_CHANGE_REQUEST.value),
            parent_id=payload.get("parent_id"),
            thread_root_id=payload.get("thread_root_id"),
            source_claude=payload.get("source_claude", ""),
            source_project=payload.get("source_project", ""),
            target_claudes=list(payload.get("target_claudes") or []),
            target_projects=list(payload.get("target_projects") or []),
            title=payload.get("title", ""),
            summary=payload.get("summary", ""),
            payload=_json_loads(payload.get("payload_json")) or {},
            media_refs=list(payload.get("media_refs") or []),
            mutable_draft=payload.get("mutable_draft"),
            source_collab_doc_id=payload.get("source_collab_doc_id"),
            created_at=payload.get("created_at", _iso_now()),
            updated_at=payload.get("updated_at"),
            expires_at=payload.get("expires_at", _iso_plus_hours(24 * 30)),
            status=payload.get("status", ProposalStatus.ECO_REVIEW.value),
            trust_required=payload.get("trust_required", ActionCategory.TACTICAL.value),
            action_category=payload.get("action_category", ActionCategory.TACTICAL.value),
            auto_routed=bool(payload.get("auto_routed", False)),
            eco_decision=_json_loads(payload.get("eco_decision_json")),
            audit_log=_json_loads(payload.get("audit_log_json")) or [],
            org_id=payload.get("org_id", ""),
            source_user_id=payload.get("source_user_id", ""),
            target_user_ids=list(payload.get("target_user_ids") or []),
            target_org_ids=list(payload.get("target_org_ids") or []),
            layer=payload.get("layer", "L3"),
            reclassify_count=int(payload.get("reclassify_count") or 0),
            is_archived=bool(payload.get("is_archived", False)),
            archived_by=list(payload.get("archived_by") or []),
            archived_at=payload.get("archived_at"),
            archive_reason=payload.get("archive_reason"),
            completed_at=payload.get("completed_at"),
            completion_note=payload.get("completion_note"),
            completion_commit_sha=payload.get("completion_commit_sha"),
            completion_artifact_ids=list(payload.get("completion_artifact_ids") or []),
            completion_result=payload.get("completion_result"),
            target_acks=_json_loads(payload.get("target_acks_json")) or {},
            last_ntfy_emit_at=payload.get("last_ntfy_emit_at"),
            # Legacy reminder_emitted key in payload (pre-2026-06-21
            # prop_iazbb2rb retirement) is silently dropped — field
            # removed from the schema.
            platform_decisions=(_json_loads(payload.get("platform_decisions_json")) or {}),
            tenant_decisions=(_json_loads(payload.get("tenant_decisions_json")) or {}),
            ser_id=payload.get("ser_id"),
            ser_state_verified=payload.get("ser_state_verified"),
            autonomy_confidence=payload.get("autonomy_confidence"),
            autonomy_rationale=payload.get("autonomy_rationale"),
            autonomy_verdict_basis=payload.get("autonomy_verdict_basis"),
            channel=payload.get("channel"),
        )

    def to_wire(self) -> dict[str, Any]:
        """Shape for API responses — inline payload instead of payload_json.

        ``decided_by_kind`` is computed here rather than stamped by callers:
        it was a handler-side addition on the inbox list only, so the single-
        get path served the same proposal without it.
        """
        return {
            "id": self.id,
            # Envelope field so no client re-derives gate transit from
            # audit-log actor strings: "human" | "system" | null (undecided).
            "decided_by_kind": decided_by_kind(self),
            "type": self.type,
            "parent_id": self.parent_id,
            "thread_root_id": self.thread_root_id,
            "source_claude": self.source_claude,
            "source_project": self.source_project,
            "target_claudes": self.target_claudes,
            "target_projects": self.target_projects,
            "title": self.title,
            "summary": self.summary,
            "payload": self.payload,
            "media_refs": self.media_refs,
            "mutable_draft": self.mutable_draft,
            "source_collab_doc_id": self.source_collab_doc_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "trust_required": self.trust_required,
            "action_category": self.action_category,
            "auto_routed": self.auto_routed,
            "eco_decision": self.eco_decision,
            "audit_log": self.audit_log,
            "source_user_id": self.source_user_id,
            "target_user_ids": self.target_user_ids,
            "target_org_ids": list(self.target_org_ids or []),
            "layer": self.layer,
            "reclassify_count": self.reclassify_count,
            "is_archived": self.is_archived,
            "archived_at": self.archived_at,
            "archive_reason": self.archive_reason,
            "archived_by": list(self.archived_by or []),
            "completed_at": self.completed_at,
            "completion_note": self.completion_note,
            "completion_commit_sha": self.completion_commit_sha,
            "completion_artifact_ids": self.completion_artifact_ids,
            "completion_result": self.completion_result,
            "target_acks": self.target_acks,
            "pending_targets": self.pending_targets(),
            "last_ntfy_emit_at": self.last_ntfy_emit_at,
            "platform_decisions": self.platform_decisions,
            "tenant_decisions": self.tenant_decisions,
            "ser_id": self.ser_id,
            "ser_state_verified": self.ser_state_verified,
            "autonomy_confidence": self.autonomy_confidence,
            "autonomy_rationale": self.autonomy_rationale,
            "autonomy_verdict_basis": self.autonomy_verdict_basis,
            "channel": self.channel,
        }


def derive_publish_aggregate_status(proposal: Proposal) -> str | None:
    """For type=publish proposals with platform_decisions populated,
    compute the aggregate proposal status from the per-platform state.

    Returns None for non-publish proposals, or when platform_decisions
    is empty (no per-platform action taken yet — caller keeps the
    proposal's existing status).

    Aggregate rules (David 2026-05-17, refined 2026-07-08 for per-
    platform dispatch fire):
    - Any expected platform missing from platform_decisions but at
      least one decided → TARGETS_PENDING. User is in the platform-
      selection stage (article already approved at proposal level).
    - All decided AND all declined → DECLINED.
    - All decided AND ≥1 accepted AND any accepted without a
      ``dispatch_id`` → ACCEPTED_PENDING_DISPATCH. Outreach picks up
      via outbox and dispatches the accepted-undispatched ones.
    - All decided AND every accepted platform HAS a ``dispatch_id`` →
      COMPLETED. Declined platforms stay un-shipped; accepted are all
      dispatched. Terminal.

    The ``dispatch_id`` field lives at ``platform_decisions[X].dispatch_id``
    and is stamped by ``cortex_complete_proposal(platform=X, ...)`` on
    successful per-platform dispatch (David 2026-07-08). Before that
    contract existed the aggregate could only ever return
    ACCEPTED_PENDING_DISPATCH once all-decided; now it forward-derives
    to COMPLETED once dispatch metadata lands, without a manual global
    complete call.
    """
    if proposal.type != ProposalType.PUBLISH.value:
        return None
    pd = proposal.platform_decisions or {}
    if not pd:
        return None
    expected_platforms = set((proposal.payload.get("platforms") or {}).keys())
    decided_platforms = set(pd.keys())
    # Some platform still pending → in the platform-selection stage.
    if expected_platforms - decided_platforms:
        return ProposalStatus.TARGETS_PENDING.value
    decisions = [(d or {}).get("decision") for d in pd.values()]
    if decisions and all(d == "declined" for d in decisions):
        return ProposalStatus.DECLINED.value
    if any(d == "accepted" for d in decisions):
        # Check per-platform dispatch state. Any accepted without a
        # dispatch_id means outreach hasn't shipped it yet — stay at
        # ACCEPTED_PENDING_DISPATCH so aggregate observers can tell
        # dispatch is still pending. Once every accepted platform has
        # a dispatch_id, aggregate promotes to COMPLETED.
        accepted_entries = [(d or {}) for d in pd.values() if (d or {}).get("decision") == "accepted"]
        undispatched = [e for e in accepted_entries if not e.get("dispatch_id")]
        if undispatched:
            return ProposalStatus.ACCEPTED_PENDING_DISPATCH.value
        return ProposalStatus.COMPLETED.value
    # All decisions are unrecognized — leave to caller
    return None


def _tenant_slug_from_canonical(canonical: str) -> str | None:
    """Extract the middle component from a canonical 3-form ai_id.

    ``<org>.<tenant>.<project>`` → ``<tenant>``. Returns None on any non-
    canonical shape (bare basename, 2-form, empty, dot-in-project-name).
    Strict-canonical: only the 3-form yields a slug — matches the wire
    contract enforced by find_users_for_ai_id.
    """
    if not canonical or not isinstance(canonical, str):
        return None
    parts = canonical.split(".", 2)
    if len(parts) != 3 or not parts[0] or not parts[1] or not parts[2]:
        return None
    return parts[1]


def expected_tenants_for_proposal(proposal: Proposal) -> set[str]:
    """Distinct target tenants for a proposal, extracted from target_claudes.

    Multi-tenant proposals have len > 1; per-tenant ECO gating applies.
    Single-tenant proposals fall through the current single-envelope
    accept path unchanged. Non-canonical target_claudes are dropped —
    they can't be routed to a per-tenant ECO topic either.
    """
    tenants: set[str] = set()
    for target in getattr(proposal, "target_claudes", None) or []:
        slug = _tenant_slug_from_canonical(target)
        if slug:
            tenants.add(slug)
    return tenants


def derive_multi_tenant_aggregate_status(
    proposal: Proposal,
) -> str | None:
    """For multi-tenant proposals with tenant_decisions populated,
    compute the aggregate proposal status from per-tenant ECO decisions.

    Returns None when:
    - proposal targets a single tenant (current envelope-level accept flow
      still applies — this function is a no-op for single-tenant proposals)
    - tenant_decisions is empty (no per-tenant ECO has acted yet — caller
      keeps the proposal's existing status)
    - proposal type is publish (per-platform derivation handles it)

    Cross-tenant ECO gating rules (David, 2026-07-08 via empirica
    prop_5mrws4tv):
    - Any expected tenant missing from tenant_decisions → ECO_REVIEW.
      One or more tenants haven't decided yet — proposal cannot promote
      even if some tenants already accepted.
    - All decided AND all declined → DECLINED.
    - All decided AND ≥1 accepted → ACCEPTED. Downstream binding
      (shared-record participants) restricts to accepted-tenant targets
      (partitioning is applied by the caller — this function only
      computes the aggregate).
    """
    if proposal.type == ProposalType.PUBLISH.value:
        return None
    expected = expected_tenants_for_proposal(proposal)
    if len(expected) <= 1:
        return None  # single-tenant — current envelope-level flow applies
    td = proposal.tenant_decisions or {}
    if not td:
        return None  # no per-tenant action yet — caller keeps status
    decided = set(td.keys())
    if expected - decided:
        return ProposalStatus.ECO_REVIEW.value
    decisions = [(td.get(t) or {}).get("decision") for t in expected]
    if decisions and all(d == "declined" for d in decisions):
        return ProposalStatus.DECLINED.value
    if any(d == "accepted" for d in decisions):
        return ProposalStatus.ACCEPTED.value
    return None


def accepted_target_claudes_for_multi_tenant(
    proposal: Proposal,
) -> list[str]:
    """Filter target_claudes to only those in accepted tenants.

    Used at downstream-binding time (shared-record participant creation)
    to enforce that a foreign-tenant ECO's decision binds only its own
    tenant's targets. Single-tenant proposals return target_claudes
    unchanged (no per-tenant partition needed).
    """
    expected = expected_tenants_for_proposal(proposal)
    if len(expected) <= 1:
        return list(proposal.target_claudes or [])
    td = proposal.tenant_decisions or {}
    accepted_tenants = {t for t in expected if (td.get(t) or {}).get("decision") == "accepted"}
    result: list[str] = []
    for target in proposal.target_claudes or []:
        slug = _tenant_slug_from_canonical(target)
        if slug and slug in accepted_tenants:
            result.append(target)
    return result


def _json_dumps(obj: Any) -> str | None:
    import json

    if obj is None:
        return None
    try:
        return json.dumps(obj, default=str)
    except Exception:
        return json.dumps(str(obj))


def _json_loads(blob: str | None) -> Any:
    import json

    if not blob:
        return None
    try:
        return json.loads(blob)
    except Exception:
        return None


def seatless_source_fallback(
    tenant: Any,
    tenant_db: Any,
    client_id: str | None = None,
) -> str:
    """What `source_claude` becomes when the caller names no seat.

    Was ``tenant.user_name`` at both propose entry points, which is wrong in
    a way canonicalization cannot fix: a USER NAME is not a practice slug,
    so ``canonicalize_ai_id_to_mesh`` passes it through untouched. Measured
    on prod 2026-09-22 — ``'David' -> 'David'``, while ``'cowork' ->
    'empirica.david.cowork'``. Ten live proposals carry a bare ``David`` or
    ``Facundo`` as their source label because of it.

    Now resolves through the SAME ladder session_init uses
    (``resolve_default_seat``), so the seat a caller is TOLD it occupies and
    the seat its work is FILED under agree by construction. A connector with
    a per-client binding gets its own seat; otherwise the user's single or
    preferred seat.

    Falls back to ``user_name`` when nothing resolves, deliberately. The
    no-seat case is a user who owns several practices and has stated no
    preference — there is no correct answer, and rejecting the emission
    outright would newly fail calls that succeed today. A bare label
    misfiles one record; a hard refusal breaks a working caller, and this
    change should not be the thing that does that.
    """
    fallback = getattr(tenant, "user_name", "") or ""
    user_id = getattr(tenant, "user_id", None)
    if not (user_id and tenant_db):
        return fallback
    try:
        seat, _status = tenant_db.resolve_default_seat(
            user_id=user_id,
            org_id=getattr(tenant, "org_id", "") or "",
            user_name=fallback,
            client_id=client_id,
        )
    except Exception:
        return fallback
    return seat or fallback

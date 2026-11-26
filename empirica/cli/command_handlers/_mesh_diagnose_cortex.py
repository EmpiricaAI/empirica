"""Cortex-side participation checks for `empirica mesh diagnose --cortex`.

Closes cortex's prop_dd3epjwqyb ask. Cross-correlates the local view
with cortex's view so silent-failure classes surface at one verb:

  - Identity & resolution: local ai_id vs cortex roster's
    ai_id_short / ai_id_mesh
  - Channels endpoint: per-tenant topic vs persisted listener
    subscription URL
  - ntfy ACL: HEAD probe with bearer to confirm READ grant
  - Mesh agreements (cross-tenant peer, when --peer set)

Read-only probes in v1. Live wake test + inbox/outbox consistency
deferred to v2 (they require emitting + MCP cortex tools).
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Check primitive ────────────────────────────────────────────────────


@dataclass
class CheckResult:
    """One probe outcome."""

    name: str
    status: str  # 'pass' | 'warn' | 'fail'
    message: str
    fix: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "message": self.message,
            "fix": self.fix,
            "details": self.details,
        }


def _pass(name: str, message: str, **details) -> CheckResult:
    return CheckResult(name=name, status="pass", message=message, details=details)


def _warn(name: str, message: str, *, fix: str | None = None, **details) -> CheckResult:
    return CheckResult(name=name, status="warn", message=message, fix=fix, details=details)


def _fail(name: str, message: str, *, fix: str | None = None, **details) -> CheckResult:
    return CheckResult(name=name, status="fail", message=message, fix=fix, details=details)


# ── HTTP helpers (no external deps) ────────────────────────────────────


def _http_get_json(
    url: str, *, api_key: str | None = None, bearer: str | None = None, timeout: float = 6.0
) -> dict[str, Any]:
    """Tiny urllib wrapper returning parsed JSON. Caller handles exceptions.

    Cortex authenticates via `Authorization: Bearer <api_key>` (matches
    the listener + practice-context flows). `api_key` and `bearer`
    are accepted interchangeably for caller ergonomics.
    """
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    token = bearer or api_key
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        raw = resp.read().decode("utf-8") or "{}"
        return json.loads(raw)


def _http_head(
    url: str,
    *,
    bearer: str | None = None,
    user: str | None = None,
    password: str | None = None,
    timeout: float = 4.0,
) -> int:
    """Probe returning HTTP status code. Uses GET (ntfy doesn't reliably
    support HEAD on poll endpoints), reads minimum bytes then closes.
    Caller handles exceptions.

    Auth precedence (matches the listener's `_ntfy_auth_header`):
    `bearer` token wins when set (revocable + preferred path); falls
    through to basic-auth `user:password` when bearer is absent. Tenants
    on basic-auth (philipp's box: ntfy.user + ntfy.password in
    credentials.yaml, no bearer token) were getting false-negative 403s
    when the probe defaulted to no-auth — fix per cortex's
    `prop_m7ns4zq3eva6rpeqcdemifksvu`.
    """
    import base64 as _base64

    req = urllib.request.Request(url, method="GET")
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    elif user or password:
        encoded = _base64.b64encode(f"{user or ''}:{password or ''}".encode()).decode("ascii")
        req.add_header("Authorization", f"Basic {encoded}")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        # Read up to 1 byte just to keep the connection clean.
        resp.read(1)
        return resp.status


# ── Probes ─────────────────────────────────────────────────────────────


def check_identity(ai_id: str, cortex_url: str, api_key: str) -> CheckResult:
    """Roster lookup for this practitioner — pass if cortex knows about us.

    Reads /v1/users/me/roster, scans `org.tenants[].projects[]` for the
    ai_id_short matching our local basename, returns the canonical
    ai_id_mesh on success.
    """
    name = "identity.roster_lookup"
    try:
        body = _http_get_json(
            f"{cortex_url.rstrip('/')}/v1/users/me/roster",
            api_key=api_key,
            timeout=8.0,
        )
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        return _fail(
            name,
            f"roster fetch failed: {type(e).__name__}: {e}",
            fix="check cortex.url in ~/.empirica/credentials.yaml and run `empirica auth login`",
        )
    self_meta = body.get("self") or {}
    self_tenant = self_meta.get("tenant_slug") or "unknown"
    org = body.get("org") or {}
    for tenant in org.get("tenants", []) or []:
        if tenant.get("tenant_slug") != self_tenant:
            continue
        for proj in tenant.get("projects", []) or []:
            if proj.get("ai_id_short") == ai_id:
                mesh = proj.get("ai_id_mesh") or "?"
                return _pass(
                    name,
                    f"resolves to {mesh}",
                    canonical=mesh,
                    ai_id_short=ai_id,
                    tenant=self_tenant,
                )
    # A practice joined as a member is listed under its owner; its address comes from /v1/users/me/projects.
    from empirica.core.loop_scheduler import content_poll

    joined = content_poll.joined_practice_canonical(cortex_url, api_key, ai_id, timeout=8.0)
    if joined:
        return _pass(
            name, f"resolves to {joined} (joined as a member)", canonical=joined, ai_id_short=ai_id, joined=True
        )
    return _fail(
        name,
        f"ai_id {ai_id!r} not found in roster for tenant {self_tenant!r}, nor among the practices this seat joined",
        fix=(
            "set .empirica/project.yaml `ai_id:` to the exact basename, then "
            "re-run setup-claude-code, or verify cortex registered this project"
        ),
    )


def check_channels_endpoint(cortex_url: str, api_key: str) -> tuple[CheckResult, str | None]:
    """Verify channels endpoint returns a per-tenant orchestration_events
    topic. Returns (CheckResult, resolved_topic_or_none) so downstream
    checks can compare subscription URLs against it.
    """
    name = "channels.orchestration_events"
    try:
        body = _http_get_json(
            f"{cortex_url.rstrip('/')}/v1/users/me/notification-channels",
            api_key=api_key,
            timeout=6.0,
        )
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        return _fail(name, f"channels fetch failed: {type(e).__name__}: {e}"), None
    channels = body.get("channels") or []
    orch_topic = None
    for ch in channels:
        kind = ch.get("category") or ch.get("kind")
        topic = ch.get("topic")
        if kind == "orchestration_events" and topic:
            orch_topic = topic
            break
        if topic and "orchestration-events" in topic:
            orch_topic = topic
            break
    if not orch_topic:
        return _fail(
            name,
            "no orchestration_events channel returned",
            fix="contact cortex maintainer — channels endpoint should advertise per-tenant topic",
        ), None
    # Per-tenant pattern: `<org>-orchestration-events-<tenant>`
    # Pre-T16/T17 per-org: `<org>-orchestration-events` (no tenant suffix)
    # Bare retired: just `orchestration-events`
    base = topic.split("?", 1)[0] if (topic := orch_topic) else ""
    if base == "orchestration-events":
        return _warn(
            name,
            f"channels advertises BARE topic {base!r} (retired)",
            fix="restart cortex; channels endpoint should advertise per-tenant",
            topic=base,
        ), orch_topic
    if base.endswith("-orchestration-events"):
        # No tenant segment — pre-T16/T17 per-org form
        return _warn(
            name,
            f"channels advertises PER-ORG topic {base!r} (pre-T16/T17)",
            fix="cortex maintainer — verify per-tenant topic rollout complete",
            topic=base,
        ), orch_topic
    # Reasonable per-tenant shape: `<something>-orchestration-events-<something>`
    if "-orchestration-events-" in base:
        return _pass(
            name,
            f"channels advertises per-tenant topic {base!r}",
            topic=base,
        ), orch_topic
    return _warn(
        name,
        f"unrecognized topic shape {base!r}",
        fix="manual review — topic doesn't match per-tenant pattern",
        topic=base,
    ), orch_topic


def check_listener_subscription_matches(
    ai_id: str,
    expected_topic: str | None,
) -> CheckResult:
    """Compare the persisted listener_active_*.json topic against what
    cortex's channels endpoint says the listener should be subscribed to.

    Fails on mismatch (silent-strand pattern). Passes if no active file
    exists (nothing to compare).
    """
    name = "listener.subscription_match"
    if not expected_topic:
        return _warn(name, "cannot compare — no expected topic from channels endpoint")
    empirica_dir = Path.home() / ".empirica"
    # listener_active files use various instance_id patterns; match by ai_id field
    candidates: list[Path] = []
    if empirica_dir.is_dir():
        for p in empirica_dir.glob("listener_active_*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("ai_id") == ai_id:
                candidates.append(p)
    if not candidates:
        return _warn(
            name,
            f"no listener_active file matches ai_id={ai_id!r} — listener may not be armed",
            fix=f"empirica listener on --ai-id {ai_id}",
        )
    expected_base = expected_topic.split("?", 1)[0].replace("ntfy:", "")
    mismatches = []
    for p in candidates:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        actual = (data.get("topic") or "").split("?", 1)[0].replace("ntfy:", "")
        if actual != expected_base:
            mismatches.append({"file": p.name, "actual": actual, "expected": expected_base})
    if mismatches:
        return _fail(
            name,
            f"{len(mismatches)} listener_active file(s) subscribed to wrong topic — silent-strand",
            fix="empirica listener gc --apply  (removes stale active files) + restart listener",
            mismatches=mismatches,
        )
    return _pass(
        name,
        f"listener_active topic matches channels endpoint ({expected_base!r})",
        topic=expected_base,
        files_checked=len(candidates),
    )


def check_ntfy_acl(
    topic: str | None,
    ntfy_url: str | None,
    ntfy_token: str | None,
    ntfy_user: str | None = None,
    ntfy_password: str | None = None,
) -> CheckResult:
    """HEAD probe of ntfy poll endpoint to confirm READ grant.

    403 = missing grant (publish-philipp pattern). 200 = grant present.
    Other codes = ntfy itself is unhappy; surface as warn.

    Auth precedence matches the listener: bearer wins; falls through to
    basic-auth user/password. Tenants on basic-auth (no token in
    credentials.yaml) were getting false-negative 403s when the probe
    defaulted to no-auth (cortex's `prop_m7ns4zq3eva6rpeqcdemifksvu`).
    """
    name = "ntfy.read_grant"
    if not topic:
        return _warn(name, "no topic to probe (channels endpoint check failed)")
    if not ntfy_url:
        return _warn(name, "ntfy.url not configured in credentials.yaml")
    base = topic.split("?", 1)[0]
    probe_url = f"{ntfy_url.rstrip('/')}/{base}/json?poll=1"
    try:
        status = _http_head(
            probe_url,
            bearer=ntfy_token,
            user=ntfy_user,
            password=ntfy_password,
            timeout=4.0,
        )
    except urllib.error.HTTPError as e:
        if e.code == 403:
            return _fail(
                name,
                f"403 Forbidden on {base!r} — missing READ grant",
                fix=(
                    "verify ntfy token + ACL provisioning on cortex side; "
                    "the bearer in credentials.yaml may not have read perms"
                ),
                topic=base,
                http_status=403,
            )
        return _warn(
            name,
            f"{e.code} {e.reason} on probe",
            fix="manual review of ntfy server state",
            topic=base,
            http_status=e.code,
        )
    except (TimeoutError, urllib.error.URLError) as e:
        return _warn(
            name,
            f"probe network error: {type(e).__name__}",
            fix="check ntfy_url reachability",
            topic=base,
        )
    if status == 200:
        return _pass(name, f"READ grant confirmed on {base!r}", topic=base, http_status=200)
    return _warn(
        name,
        f"unexpected HTTP {status} on probe",
        topic=base,
        http_status=status,
    )


def check_mesh_agreement(
    peer_canonical: str,
    cortex_url: str,
    api_key: str,
) -> CheckResult:
    """Verify an active mesh_sharing_agreement row exists for the peer.

    Only fires when --peer flag is set. Cross-tenant peers require an
    explicit agreement; same-tenant peers don't.
    """
    name = "mesh.agreement"
    try:
        body = _http_get_json(
            f"{cortex_url.rstrip('/')}/v1/orgs/me/mesh_sharing_agreements",
            api_key=api_key,
            timeout=6.0,
        )
    except (TimeoutError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
        return _fail(
            name,
            f"agreements fetch failed: {type(e).__name__}: {e}",
            fix="check cortex.url in credentials.yaml and run `empirica auth login`",
            peer=peer_canonical,
        )
    agreements = body.get("agreements") or body.get("rows") or []
    for row in agreements:
        # Match by either source/target order; agreements are bidirectional
        endpoints = {
            row.get("source_practice"),
            row.get("target_practice"),
            row.get("peer"),
            row.get("from"),
            row.get("to"),
        }
        if peer_canonical in endpoints and row.get("active") is not False:
            return _pass(
                name,
                f"active agreement with {peer_canonical!r}",
                peer=peer_canonical,
            )
    return _fail(
        name,
        f"no active mesh_sharing_agreement with {peer_canonical!r}",
        fix="cortex admin: provision the bilateral agreement for this peer pair",
        peer=peer_canonical,
    )


# ── Orchestration ──────────────────────────────────────────────────────


def run_cortex_checks(
    ai_id: str,
    *,
    cortex_url: str,
    api_key: str,
    ntfy_url: str | None = None,
    ntfy_token: str | None = None,
    ntfy_user: str | None = None,
    ntfy_password: str | None = None,
    peer: str | None = None,
) -> list[CheckResult]:
    """Run all read-only cortex-participation checks in order.

    Order matters: identity → channels (returns expected topic) →
    listener subscription match (uses expected topic) → ntfy ACL (uses
    expected topic) → mesh agreement (only with --peer).
    """
    results: list[CheckResult] = []
    results.append(check_identity(ai_id, cortex_url, api_key))
    channels_result, expected_topic = check_channels_endpoint(cortex_url, api_key)
    results.append(channels_result)
    results.append(check_listener_subscription_matches(ai_id, expected_topic))
    results.append(
        check_ntfy_acl(
            expected_topic,
            ntfy_url,
            ntfy_token,
            ntfy_user=ntfy_user,
            ntfy_password=ntfy_password,
        )
    )
    if peer:
        results.append(check_mesh_agreement(peer, cortex_url, api_key))
    return results


_BOX_WIDTH = 90  # outer width including the two │ borders


def _wrap_message(prefix_width: int, msg: str, *, max_inner: int) -> list[str]:
    """Word-wrap a message so each line fits within max_inner chars, on the
    column to the right of the {glyph}{name} prefix. Returns the message
    chunks (first chunk goes on the prefix line, subsequent chunks indent
    under the message column).
    """
    available = max_inner - prefix_width
    if available <= 0:
        return [msg]
    chunks: list[str] = []
    remaining = msg
    while remaining:
        if len(remaining) <= available:
            chunks.append(remaining)
            break
        # Break on the last space before `available`, fall back to hard cut.
        cut = remaining.rfind(" ", 0, available + 1)
        if cut <= 0:
            cut = available
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


def render_results_human(results: list[CheckResult]) -> str:
    """Box-drawing render per cortex's prop_dd3epjwqyb spec.

    Lines wrap rather than overflow the box, and every content line carries
    a closing │ so the visual frame is intact even at narrower terminals.
    """
    inner_width = _BOX_WIDTH - 2  # subtract the two │ borders
    title = " Cortex participation "
    header_pad = inner_width - len(title) - 1  # one leading ─
    lines = [f"╭─{title}{'─' * header_pad}╮"]
    for r in results:
        glyph = {"pass": "✓", "warn": "⚠", "fail": "✗"}.get(r.status, "?")
        prefix = f" {glyph} {r.name:32s} "
        chunks = _wrap_message(len(prefix), r.message, max_inner=inner_width)
        first = chunks[0] if chunks else ""
        line_body = f"{prefix}{first}".ljust(inner_width)
        lines.append(f"│{line_body}│")
        # Continuation chunks indent under the message column.
        cont_indent = " " * len(prefix)
        for cont in chunks[1:]:
            cont_body = f"{cont_indent}{cont}".ljust(inner_width)
            lines.append(f"│{cont_body}│")
        if r.fix:
            fix_prefix = "   → "
            fix_chunks = _wrap_message(
                len(fix_prefix),
                r.fix,
                max_inner=inner_width,
            )
            for i, chunk in enumerate(fix_chunks):
                pre = fix_prefix if i == 0 else " " * len(fix_prefix)
                body = f"{pre}{chunk}".ljust(inner_width)
                lines.append(f"│{body}│")
    lines.append(f"╰{'─' * inner_width}╯")
    return "\n".join(lines)


def aggregate_exit_code(results: list[CheckResult]) -> int:
    """Any FAIL → 2, any WARN-only → 1, all PASS → 0."""
    if any(r.status == "fail" for r in results):
        return 2
    if any(r.status == "warn" for r in results):
        return 1
    return 0

"""Markdown rendering for v2 bootstrap payload sections.

Used by session-init.py and post-compact.py hooks to inject v2 sections
(persistent_reference + topic_relevant_backlog) into the existing
EPISTEMIC FOCUS block. Active state (circle 1) is already covered by
the legacy `_load_dynamic_context` rendering — we don't re-render it
here to avoid duplication.

Usage:
    from empirica.core.bootstrap import build_bootstrap_payload
    from empirica.core.bootstrap.render import render_v2_supplemental

    payload = build_bootstrap_payload(project_path)
    md = render_v2_supplemental(payload)
    # Append `md` to the hook's additionalContext
"""

from __future__ import annotations


def render_v2_supplemental(payload: dict) -> str:  # noqa: C901 — long if-block per section is the simplest shape
    """Render persistent_reference + topic_relevant_backlog sections only.

    Returns markdown ready to append to MEMORY.md / additionalContext.
    Empty string if both sections are empty (don't add a noisy header).
    """
    parts: list[str] = []

    # Persistent reference (circle 2) — always-visible structural items
    pr = payload.get("persistent_reference", {})
    pr_total = sum(len(v) for v in pr.values() if isinstance(v, list))
    if pr_total > 0:
        parts.append("## PERSISTENT REFERENCE")
        parts.append(
            "*Load-bearing items the AI cannot easily auto-retrieve. "
            "These are the project's structural knowledge — accept as ground truth.*\n"
        )

        decisions = pr.get("decisions_with_active_outcome", [])
        if decisions:
            parts.append("**Active decisions** (no outcome yet — rationale still load-bearing):")
            for d in decisions[:10]:
                choice = (d.get("choice") or "")[:120]
                rationale = (d.get("rationale") or "")[:80]
                parts.append(f"- **{choice}** — {rationale}")
            parts.append("")

        verified = pr.get("verified_assumptions", [])
        if verified:
            parts.append("**Verified / falsified assumptions** (now ground truth):")
            for a in verified[:10]:
                summary = a.get("summary") or (a.get("body") or "")[:120]
                status = a.get("status", "")
                parts.append(f"- [{status}] {summary}")
            parts.append("")

        sources = pr.get("sources", [])
        if sources:
            parts.append("**Sources** (citation base):")
            for s in sources[:10]:
                title = s.get("title") or "(untitled)"
                url = s.get("url") or ""
                if url:
                    parts.append(f"- [{title}]({url})")
                else:
                    parts.append(f"- {title}")
            parts.append("")

    # Topic-relevant backlog (circle 3) — similarity-pulled
    tb = payload.get("topic_relevant_backlog", {})
    tb_total = sum(len(v) for v in tb.values() if isinstance(v, list))
    topic = payload.get("active_topic", {})
    if tb_total > 0:
        parts.append("## TOPIC-RELEVANT BACKLOG")
        topic_src = topic.get("source", "?")
        threshold = topic.get("similarity_threshold", "?")
        parts.append(
            f"*Pulled by similarity to active topic (source: {topic_src}, "
            f"threshold: {threshold}). Decide if relevant — your judgment overrides.*\n"
        )

        unknowns = tb.get("open_unknowns", [])
        if unknowns:
            parts.append("**Open unknowns matching topic** — backlog questions you might address:")
            for u in unknowns[:5]:
                summary = u.get("summary") or (u.get("body") or "")[:120]
                sim = u.get("similarity_score")
                if sim is not None:
                    parts.append(f"- {summary}  *(sim: {sim:.2f})*")
                else:
                    parts.append(f"- {summary}")
            parts.append("")

        assumptions = tb.get("open_assumptions", [])
        if assumptions:
            parts.append("**Unverified assumptions matching topic**:")
            for a in assumptions[:5]:
                summary = a.get("summary") or (a.get("body") or "")[:120]
                conf = a.get("confidence", "?")
                parts.append(f"- {summary}  *(confidence: {conf})*")
            parts.append("")

        planned = tb.get("planned_goals", [])
        if planned:
            parts.append("**Planned goals matching topic** (queued work):")
            for g in planned[:5]:
                obj = (g.get("objective") or "")[:120]
                parts.append(f"- {obj}")
            parts.append("")

        completed = tb.get("completed_goals_relevant", [])
        if completed:
            parts.append("**⚠ Completed goals matching topic** (anti-clobber — you've done this):")
            for g in completed[:3]:
                obj = (g.get("objective") or "")[:120]
                done_at = (g.get("completed_at") or "")[:10]
                parts.append(f"- ✓ {obj}  *(completed {done_at})*")
            parts.append("")

        resolved = tb.get("resolved_unknowns_relevant", [])
        if resolved:
            parts.append("**Resolved unknowns matching topic** (the resolutions are findings):")
            for u in resolved[:5]:
                summary = u.get("summary") or (u.get("body") or "")[:120]
                resolved_by = (u.get("resolved_by") or "")[:80]
                parts.append(f"- ✓ {summary}  *(by: {resolved_by})*")
            parts.append("")

        dead_ends = tb.get("dead_ends_relevant", [])
        if dead_ends:
            parts.append("**Dead-ends matching topic** (don't re-try these):")
            for d in dead_ends[:3]:
                approach = (d.get("approach") or "")[:120]
                why = (d.get("why_failed") or "")[:80]
                parts.append(f"- ✗ {approach} — {why}")
            parts.append("")

    if not parts:
        return ""
    return "\n".join(parts)

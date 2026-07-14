"""
Generate browsable .empirica/ audit trail from git notes.

Reads epistemic data from git notes (canonical source) + cold storage
and generates consolidated markdown files — one per artifact type.

Output structure:
    .empirica/
    ├── README.md           # Project epistemic dashboard
    ├── findings.md         # All findings (knowledge artifacts)
    ├── unknowns.md         # Open questions
    ├── dead-ends.md        # Failed approaches
    ├── mistakes.md         # Errors with prevention
    ├── goals.md            # Work units with tasks
    ├── transactions.md     # PREFLIGHT→CHECK→POSTFLIGHT trajectories
    ├── sessions.md         # Session timeline
    ├── handoffs.md         # Session continuity reports
    ├── cascades.md         # Investigation decision logs
    ├── lessons.md          # Procedural knowledge (cold storage)
    ├── sources.md          # Reference documents
    ├── signatures.md       # Cryptographic provenance
    └── calibration.md      # Bias corrections, drift state
"""

import json
import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


# ── Utility ──


def _short_id(uuid_str):
    """First 8 chars of UUID for anchors."""
    return str(uuid_str)[:8] if uuid_str else "unknown"


def _format_date(ts):
    """Format timestamp to human-readable date."""
    if not ts:
        return "unknown"
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            return str(ts)
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return ts[:16] if len(ts) > 16 else ts
    return str(ts)


def _format_date_short(ts):
    """Format timestamp to date only."""
    full = _format_date(ts)
    return full[:10] if len(full) >= 10 else full


def _truncate(text, length=100):
    """Truncate text with ellipsis."""
    if not text:
        return ""
    text = str(text).replace("\n", " ").strip()
    return text[:length] + "\u2026" if len(text) > length else text


def _vector_bar(value):
    """Render a vector value as mini bar."""
    if value is None:
        return ""
    v = float(value)
    n = int(v * 10)
    return "\u2588" * n + "\u2591" * (10 - n) + f" {v:.2f}"


def _get_ts(data):
    """Extract sortable timestamp from data dict."""
    ts = data.get("created_at") or data.get("created_timestamp") or data.get("timestamp") or 0
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0
    return float(ts) if ts else 0


# ── Git Notes Reader ──


def _read_note_blob(workspace, ref):
    """Read JSON content from a git notes ref.

    Git notes refs point to commits whose trees contain blobs.
    Walk: ref → commit → tree → first blob → JSON content.
    """
    try:
        tree_result = subprocess.run(
            ["git", "cat-file", "-p", ref], cwd=workspace, capture_output=True, text=True, timeout=10
        )
        if tree_result.returncode != 0:
            return None

        tree_sha = None
        for cline in tree_result.stdout.split("\n"):
            if cline.startswith("tree "):
                tree_sha = cline.split()[1]
                break
        if not tree_sha:
            return None

        ls_result = subprocess.run(
            ["git", "ls-tree", tree_sha], cwd=workspace, capture_output=True, text=True, timeout=10
        )
        if ls_result.returncode != 0 or not ls_result.stdout.strip():
            return None

        first_line = ls_result.stdout.strip().split("\n")[0]
        blob_sha = first_line.split()[2]

        blob_result = subprocess.run(
            ["git", "cat-file", "-p", blob_sha], cwd=workspace, capture_output=True, text=True, timeout=10
        )
        if blob_result.returncode == 0 and blob_result.stdout.strip():
            return json.loads(blob_result.stdout.strip())
    except (json.JSONDecodeError, subprocess.TimeoutExpired, IndexError, ValueError):
        pass
    return None


def _read_note_raw(workspace, ref):
    """Read raw text from a git notes ref (for non-JSON formats like cascades)."""
    try:
        tree_result = subprocess.run(
            ["git", "cat-file", "-p", ref], cwd=workspace, capture_output=True, text=True, timeout=10
        )
        if tree_result.returncode != 0:
            return None

        tree_sha = None
        for cline in tree_result.stdout.split("\n"):
            if cline.startswith("tree "):
                tree_sha = cline.split()[1]
                break
        if not tree_sha:
            return None

        ls_result = subprocess.run(
            ["git", "ls-tree", tree_sha], cwd=workspace, capture_output=True, text=True, timeout=10
        )
        if ls_result.returncode != 0 or not ls_result.stdout.strip():
            return None

        first_line = ls_result.stdout.strip().split("\n")[0]
        blob_sha = first_line.split()[2]

        blob_result = subprocess.run(
            ["git", "cat-file", "-p", blob_sha], cwd=workspace, capture_output=True, text=True, timeout=10
        )
        if blob_result.returncode == 0:
            return blob_result.stdout
    except (subprocess.TimeoutExpired, IndexError):
        pass
    return None


def _read_all_notes(workspace, namespace):
    """Read all notes under a namespace. Returns list of (id, data_dict)."""
    items = []
    try:
        result = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", f"refs/notes/empirica/{namespace}/"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return items

        for ref in result.stdout.strip().split("\n"):
            ref = ref.strip()
            if not ref:
                continue
            item_id = ref.split("/")[-1]
            data = _read_note_blob(workspace, ref)
            if data:
                items.append((item_id, data))
    except subprocess.TimeoutExpired:
        logger.warning(f"Timeout reading git notes for {namespace}")
    return items


def _read_session_refs(workspace):
    """Read all session epistemic refs. Returns dict: session_id -> list of checkpoint dicts."""
    sessions = {}
    try:
        result = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/notes/empirica/session/"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return sessions

        for ref in result.stdout.strip().split("\n"):
            ref = ref.strip()
            if not ref:
                continue
            # ref: refs/notes/empirica/session/{session_id}/{PHASE}/{round}
            parts = ref.split("/")
            if len(parts) >= 7:
                sid = parts[4]
                data = _read_note_blob(workspace, ref)
                if data:
                    sessions.setdefault(sid, []).append(data)
    except subprocess.TimeoutExpired:
        logger.warning("Timeout reading session refs")
    return sessions


def _read_cascade_refs(workspace):
    """Read cascade (investigation decision) refs. Returns list of (transaction_id, entries)."""
    cascades = []
    try:
        result = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/notes/empirica/cascades/"],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return cascades

        for ref in result.stdout.strip().split("\n"):
            ref = ref.strip()
            if not ref:
                continue
            parts = ref.split("/")
            # refs/notes/empirica/cascades/{session_id}/{transaction_id}
            tid = parts[-1] if len(parts) >= 6 else ref.split("/")[-1]
            sid = parts[4] if len(parts) >= 6 else "unknown"

            raw = _read_note_raw(workspace, ref)
            if raw:
                entries = []
                for line in raw.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    # Format: LABEL: {json}
                    colon_pos = line.find(":")
                    if colon_pos > 0:
                        label = line[:colon_pos].strip()
                        try:
                            payload = json.loads(line[colon_pos + 1 :].strip())
                            entries.append({"decision": label, "data": payload})
                        except json.JSONDecodeError:
                            entries.append({"decision": label, "data": {"raw": line[colon_pos + 1 :].strip()}})
                    else:
                        entries.append({"decision": "UNKNOWN", "data": {"raw": line}})
                cascades.append((sid, tid, entries))
    except subprocess.TimeoutExpired:
        logger.warning("Timeout reading cascade refs")
    return cascades


def _read_lessons(workspace):
    """Read lessons from YAML cold storage."""
    lessons = []
    lessons_dir = Path(workspace) / ".empirica" / "lessons"
    if not lessons_dir.exists():
        return lessons
    for f in lessons_dir.glob("*.yaml"):
        try:
            with open(f) as fh:
                data = yaml.safe_load(fh)
                if data:
                    lessons.append((f.stem, data))
        except (OSError, yaml.YAMLError):
            pass
    return lessons


def _read_ref_docs(workspace):
    """Read reference documents listing."""
    docs = []
    ref_dir = Path(workspace) / ".empirica" / "ref-docs"
    if not ref_dir.exists():
        return docs
    for f in sorted(ref_dir.iterdir()):
        if f.is_file() and not f.name.startswith("."):
            docs.append((f.name, {"path": str(f), "size": f.stat().st_size}))
    return docs


def _read_calibration(workspace):
    """Read calibration data from .breadcrumbs.yaml."""
    bc_path = Path(workspace) / ".breadcrumbs.yaml"
    if not bc_path.exists():
        return None
    try:
        with open(bc_path) as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError):
        return None


# ── Markdown Generators (one function per artifact type) ──


def _render_findings(findings):
    """Render findings.md — all findings sorted by date descending."""
    findings.sort(key=lambda x: _get_ts(x[1]), reverse=True)

    lines = [
        "# Findings",
        "",
        "> Knowledge artifacts — discoveries, validated learnings, confirmed facts.",
        f"> {len(findings)} total",
        "",
        "| Date | Impact | Finding | AI | Session |",
        "|------|--------|---------|-----|---------|",
    ]
    for fid, data in findings:
        short = _short_id(fid)
        impact = data.get("impact", 0.5)
        text = _truncate(data.get("finding", data.get("finding_data", {}).get("finding", "")), 80)
        ai = data.get("ai_id", "?")
        date = _format_date_short(data.get("created_at") or data.get("created_timestamp", ""))
        sid = _short_id(data.get("session_id", ""))
        lines.append(
            f"| {date} | {float(impact):.1f} | "
            f'<a id="finding-{short}"></a>{text} | '
            f"`{ai}` | [{sid}](transactions.md#{sid}) |"
        )

    lines.extend(["", "---", "*Generated by [Empirica](https://getempirica.com)*"])
    return "\n".join(lines)


def _render_unknowns(unknowns):
    """Render unknowns.md — open questions and resolved ones."""
    unknowns.sort(key=lambda x: _get_ts(x[1]), reverse=True)

    lines = [
        "# Unknowns",
        "",
        "> Open questions, gaps, and areas needing investigation.",
        f"> {len(unknowns)} total",
        "",
        "| Date | Status | Unknown | AI | Session |",
        "|------|--------|---------|-----|---------|",
    ]
    for uid, data in unknowns:
        short = _short_id(uid)
        resolved = data.get("resolved") or data.get("is_resolved", False)
        status = "\u2705" if resolved else "\U0001f534"
        text = _truncate(data.get("unknown", ""), 80)
        ai = data.get("ai_id", "?")
        date = _format_date_short(data.get("created_at") or data.get("created_timestamp", ""))
        sid = _short_id(data.get("session_id", ""))
        resolved_by = data.get("resolved_by", "")
        extra = f" *Resolved: {_truncate(resolved_by, 40)}*" if resolved_by else ""
        lines.append(
            f"| {date} | {status} | "
            f'<a id="unknown-{short}"></a>{text}{extra} | '
            f"`{ai}` | [{sid}](transactions.md#{sid}) |"
        )

    lines.extend(["", "---", "*Generated by [Empirica](https://getempirica.com)*"])
    return "\n".join(lines)


def _render_dead_ends(dead_ends):
    """Render dead-ends.md — failed approaches to prevent re-exploration."""
    dead_ends.sort(key=lambda x: _get_ts(x[1]), reverse=True)

    lines = [
        "# Dead Ends",
        "",
        "> Approaches that were tried and failed. Prevents re-exploration.",
        f"> {len(dead_ends)} total",
        "",
    ]
    for did, data in dead_ends:
        short = _short_id(did)
        approach = data.get("approach", "")
        why_failed = data.get("why_failed", "")
        ai = data.get("ai_id", "?")
        date = _format_date_short(data.get("created_at") or data.get("created_timestamp", ""))
        sid = _short_id(data.get("session_id", ""))

        lines.extend(
            [
                f'<a id="deadend-{short}"></a>',
                f"### {_truncate(approach, 80)}",
                "",
                f"**Why it failed:** {why_failed}",
                "",
                f"`{ai}` | {date} | Session [{sid}](transactions.md#{sid})",
                "",
                "---",
                "",
            ]
        )

    lines.append("*Generated by [Empirica](https://getempirica.com)*")
    return "\n".join(lines)


def _render_mistakes(mistakes):
    """Render mistakes.md — errors with root cause and prevention."""
    mistakes.sort(key=lambda x: _get_ts(x[1]), reverse=True)

    lines = [
        "# Mistakes",
        "",
        "> Errors made with root cause analysis and prevention strategies.",
        f"> {len(mistakes)} total",
        "",
    ]
    for mid, data in mistakes:
        short = _short_id(mid)
        mistake = data.get("mistake", "")
        why_wrong = data.get("why_wrong", "")
        prevention = data.get("prevention", "")
        cost = data.get("cost_estimate", "")
        root_cause = data.get("root_cause_vector", "")
        ai = data.get("ai_id", "?")
        date = _format_date_short(data.get("created_at") or data.get("created_timestamp", ""))
        sid = _short_id(data.get("session_id", ""))

        lines.extend(
            [
                f'<a id="mistake-{short}"></a>',
                f"### {_truncate(mistake, 80)}",
                "",
                f"**Why wrong:** {why_wrong}",
                "",
                f"**Prevention:** {prevention}",
                "",
            ]
        )
        meta_parts = [f"`{ai}`", date, f"Session [{sid}](transactions.md#{sid})"]
        if cost:
            meta_parts.append(f"Cost: {cost}")
        if root_cause:
            meta_parts.append(f"Root cause: `{root_cause}`")
        lines.extend([" | ".join(meta_parts), "", "---", ""])

    lines.append("*Generated by [Empirica](https://getempirica.com)*")
    return "\n".join(lines)


def _render_goals(goals, tasks_by_goal):
    """Render goals.md — all goals with tasks inline."""
    # Sort by most recent first
    goals.sort(key=lambda x: _get_ts(x[1]), reverse=True)

    lines = [
        "# Goals",
        "",
        "> Work units with tasks and epistemic tracking.",
        f"> {len(goals)} total",
        "",
    ]

    for gid, data in goals:
        short = _short_id(gid)
        goal_data = data.get("goal_data", {})
        objective = goal_data.get("objective", "") or data.get("objective", "")
        scope = goal_data.get("scope", {}) or data.get("scope", {})
        lineage = data.get("lineage", [])
        ai_id = data.get("ai_id", "unknown")
        date = _format_date_short(data.get("created_at") or data.get("created_timestamp", ""))
        sid = _short_id(data.get("session_id", ""))

        # Get tasks from the tasks namespace
        tasks = tasks_by_goal.get(gid, [])
        total = len(tasks)
        completed = sum(1 for s in tasks if s.get("completed_timestamp"))
        pct = int(completed / total * 100) if total > 0 else 0
        progress = f"{completed}/{total} ({pct}%)" if total > 0 else "no tasks"

        lines.extend(
            [
                f'<a id="goal-{short}"></a>',
                f"### {objective or '(no objective)'}",
                "",
                f"**Progress:** {progress} | **AI:** `{ai_id}` | **Date:** {date} | **Session:** [{sid}](transactions.md#{sid})",
                "",
            ]
        )

        if scope and isinstance(scope, dict):
            lines.append(
                f"Scope: breadth={scope.get('breadth', '?')} "
                f"duration={scope.get('duration', '?')} "
                f"coordination={scope.get('coordination', '?')}"
            )
            lines.append("")

        if tasks:
            for st in tasks:
                done = bool(st.get("completed_timestamp"))
                icon = "\u2705" if done else "\u2b1c"
                desc = _truncate(st.get("description", ""), 100)
                imp = st.get("epistemic_importance", "")
                imp_tag = f" `{imp}`" if imp else ""
                lines.append(f"- {icon} {desc}{imp_tag}")
            lines.append("")

        if lineage and len(lineage) > 1:
            lines.append("<details><summary>Lineage</summary>")
            lines.append("")
            for entry in lineage:
                lines.append(
                    f"- `{entry.get('ai_id', '?')}` {entry.get('action', '?')} "
                    f"({_format_date_short(entry.get('timestamp', ''))})"
                )
            lines.extend(["", "</details>", ""])

        lines.extend(["---", ""])

    lines.append("*Generated by [Empirica](https://getempirica.com)*")
    return "\n".join(lines)


def _render_transactions(sessions_map):
    """Render transactions.md — PREFLIGHT→CHECK→POSTFLIGHT trajectories."""
    phase_order = {"PREFLIGHT": 0, "CHECK": 1, "ACT": 2, "POSTFLIGHT": 3}

    # Sort sessions by earliest timestamp
    sorted_sessions = sorted(
        sessions_map.items(), key=lambda x: min((_get_ts(cp) for cp in x[1]), default=0), reverse=True
    )

    lines = [
        "# Epistemic Transactions",
        "",
        "> PREFLIGHT \u2192 CHECK \u2192 POSTFLIGHT trajectories with 13-vector epistemic snapshots.",
        f"> {len(sessions_map)} sessions, {sum(len(v) for v in sessions_map.values())} checkpoints",
        "",
    ]

    for sid, checkpoints in sorted_sessions:
        short = _short_id(sid)
        checkpoints.sort(key=lambda c: (c.get("round") or 0, phase_order.get(c.get("phase", ""), 99)))

        first = checkpoints[0] if checkpoints else {}
        ai_id = first.get("ai_id", "unknown")
        date = _format_date(first.get("timestamp", ""))

        lines.extend(
            [
                f'<a id="{short}"></a>',
                f"## Session {short}",
                "",
                f"**AI:** `{ai_id}` | **Started:** {date} | **Checkpoints:** {len(checkpoints)}",
                "",
            ]
        )

        for cp in checkpoints:
            phase = cp.get("phase", "?")
            rnd = cp.get("round") or 1
            vectors = cp.get("vectors", {})
            reasoning = cp.get("reasoning", "") or (cp.get("meta", {}) or {}).get("reasoning", "")
            decision = cp.get("decision", "")
            confidence = cp.get("overall_confidence", "")

            lines.append(f"### {phase} (Round {rnd})")
            lines.append("")

            if decision:
                lines.append(f"**Decision:** `{decision}`")
                lines.append("")
            if confidence:
                lines.append(f"**Overall confidence:** {confidence}")
                lines.append("")

            if vectors:
                lines.extend(["| Vector | Value |", "|--------|-------|"])
                for k in [
                    "know",
                    "uncertainty",
                    "engagement",
                    "do",
                    "context",
                    "clarity",
                    "coherence",
                    "signal",
                    "density",
                    "state",
                    "change",
                    "completion",
                    "impact",
                ]:
                    v = vectors.get(k)
                    if v is not None:
                        lines.append(f"| {k} | {_vector_bar(v)} |")
                lines.append("")

            # Show deltas if this is POSTFLIGHT
            deltas = (cp.get("meta", {}) or {}).get("deltas", {})
            if deltas:
                lines.append("**Learning delta:**")
                lines.append("")
                delta_parts = []
                for k, v in sorted(deltas.items()):
                    if isinstance(v, (int, float)) and abs(v) > 0.001:
                        sign = "+" if v > 0 else ""
                        delta_parts.append(f"{k}: {sign}{v:.2f}")
                if delta_parts:
                    lines.append(", ".join(delta_parts))
                    lines.append("")

            if reasoning:
                lines.extend(
                    [
                        "<details><summary>Reasoning</summary>",
                        "",
                        reasoning,
                        "",
                        "</details>",
                        "",
                    ]
                )

        lines.extend(["---", ""])

    lines.append("*Generated by [Empirica](https://getempirica.com)*")
    return "\n".join(lines)


def _render_sessions(sessions_map):
    """Render sessions.md — timeline overview (summary, not full vectors)."""
    sorted_sessions = sorted(
        sessions_map.items(), key=lambda x: min((_get_ts(cp) for cp in x[1]), default=0), reverse=True
    )

    lines = [
        "# Sessions",
        "",
        "> Work sessions with epistemic state snapshots.",
        f"> {len(sessions_map)} total",
        "",
        "| Session | AI | Phases | Date | Confidence |",
        "|---------|-----|--------|------|------------|",
    ]

    for sid, checkpoints in sorted_sessions:
        short = _short_id(sid)
        first = checkpoints[0] if checkpoints else {}
        ai = first.get("ai_id", "?")
        date = _format_date_short(first.get("timestamp", ""))
        phases = sorted({c.get("phase", "?") for c in checkpoints})
        phase_str = " \u2192 ".join(phases)
        conf = first.get("overall_confidence", "")
        conf_str = f"{conf:.2f}" if isinstance(conf, (int, float)) else str(conf)
        lines.append(f"| [{short}](transactions.md#{short}) | `{ai}` | {phase_str} | {date} | {conf_str} |")

    lines.extend(["", "---", "*Generated by [Empirica](https://getempirica.com)*"])
    return "\n".join(lines)


def _render_handoffs(handoffs):
    """Render handoffs.md — session continuity reports."""
    handoffs.sort(key=lambda x: _get_ts(x[1]), reverse=True)

    lines = [
        "# Handoff Reports",
        "",
        "> Session continuity reports for context transfer between sessions.",
        f"> {len(handoffs)} total",
        "",
    ]

    for hid, data in handoffs:
        short = _short_id(hid)
        # Handoffs use compressed keys: s, ai, ts, task, dur, deltas, findings, gaps, unknowns, next
        sid = data.get("s", data.get("session_id", ""))
        ai = data.get("ai", data.get("ai_id", "?"))
        ts = data.get("ts", data.get("timestamp", ""))
        task = data.get("task", data.get("task_summary", ""))
        duration = data.get("dur", data.get("duration", ""))
        findings = data.get("findings", data.get("key_findings", []))
        unknowns_list = data.get("unknowns", data.get("remaining_unknowns", []))
        next_ctx = data.get("next", data.get("next_session_context", ""))
        cal = data.get("cal", data.get("calibration", ""))

        lines.extend(
            [
                f'<a id="handoff-{short}"></a>',
                f"### Session {_short_id(sid)} \u2192 next",
                "",
                f"**AI:** `{ai}` | **Date:** {_format_date(ts)}",
            ]
        )
        if duration:
            lines.append(
                f" | **Duration:** {duration:.0f}s"
                if isinstance(duration, (int, float))
                else f" | **Duration:** {duration}"
            )
        lines.append("")

        if task:
            lines.extend([f"**Summary:** {task}", ""])

        if findings:
            lines.append("**Key findings:**")
            for f in findings:
                lines.append(f"- {f}")
            lines.append("")

        if unknowns_list:
            lines.append("**Remaining unknowns:**")
            for u in unknowns_list:
                lines.append(f"- {u}")
            lines.append("")

        if next_ctx:
            lines.extend([f"**Next session context:** {next_ctx}", ""])

        if cal:
            lines.extend([f"**Calibration:** {cal}", ""])

        lines.extend(["---", ""])

    lines.append("*Generated by [Empirica](https://getempirica.com)*")
    return "\n".join(lines)


def _render_cascades(cascades):
    """Render cascades.md — investigation decision logs."""
    lines = [
        "# Investigation Cascades",
        "",
        "> INVESTIGATE/PROCEED decision logs from CHECK gates.",
        f"> {len(cascades)} cascade{'s' if len(cascades) != 1 else ''}",
        "",
    ]

    for sid, tid, entries in cascades:
        lines.extend(
            [
                f'<a id="cascade-{_short_id(tid)}"></a>',
                f"## Session {_short_id(sid)} / Transaction {_short_id(tid)}",
                "",
            ]
        )

        for _i, entry in enumerate(entries):
            decision = entry.get("decision", "?")
            payload = entry.get("data", {})
            ts = payload.get("timestamp", "")
            findings = payload.get("findings", [])

            icon = "\U0001f50d" if decision == "INVESTIGATE" else "\u2705" if decision == "PROCEED" else "\u2753"
            lines.append(f"### {icon} {decision}")
            if ts:
                lines.append(f"*{_format_date(ts)}*")
            lines.append("")

            if findings:
                for f in findings:
                    lines.append(f"- {_truncate(f, 120)}")
                lines.append("")

        lines.extend(["---", ""])

    lines.append("*Generated by [Empirica](https://getempirica.com)*")
    return "\n".join(lines)


def _render_lessons(lessons):
    """Render lessons.md — procedural knowledge from YAML cold storage."""
    lines = [
        "# Lessons",
        "",
        "> Procedural knowledge (antibodies) from cold storage.",
        "> Lessons decay when contradicted by new findings.",
        f"> {len(lessons)} total",
        "",
    ]

    for lid, data in lessons:
        title = data.get("title", lid)
        domain = data.get("domain", "general")
        confidence = data.get("source_confidence", data.get("confidence", "?"))
        trigger = data.get("trigger", "")
        procedure = data.get("procedure", "")
        anti_pattern = data.get("anti_pattern", "")
        created = data.get("created_at", "")

        lines.extend(
            [
                f'<a id="lesson-{lid[:8]}"></a>',
                f"### {title}",
                "",
                f"**Domain:** `{domain}` | **Confidence:** {confidence} | **Created:** {_format_date_short(created)}",
                "",
            ]
        )

        if trigger:
            lines.extend([f"**Trigger:** {trigger}", ""])
        if procedure:
            if isinstance(procedure, list):
                for step in procedure:
                    lines.append(f"1. {step}")
            else:
                lines.append(str(procedure))
            lines.append("")
        if anti_pattern:
            lines.extend([f"**Anti-pattern:** {anti_pattern}", ""])

        lines.extend(["---", ""])

    lines.append("*Generated by [Empirica](https://getempirica.com)*")
    return "\n".join(lines)


def _render_sources(ref_docs):
    """Render sources.md — reference documents."""
    lines = [
        "# Sources",
        "",
        "> Reference documents added to project knowledge base.",
        f"> {len(ref_docs)} total",
        "",
        "| Document | Size |",
        "|----------|------|",
    ]

    for name, data in ref_docs:
        size = data.get("size", 0)
        size_str = f"{size:,}" if size else "?"
        lines.append(f'| <a id="source-{name[:8]}"></a>{name} | {size_str} bytes |')

    lines.extend(["", "---", "*Generated by [Empirica](https://getempirica.com)*"])
    return "\n".join(lines)


def _render_signatures(signatures):
    """Render signatures.md — cryptographic provenance chain."""
    lines = [
        "# Signatures",
        "",
        "> Ed25519 cryptographic signatures of epistemic checkpoints.",
        f"> {len(signatures)} total",
        "",
    ]

    for sig_id, data in signatures:
        short = _short_id(sig_id)
        ai = data.get("ai_id", "?")
        signed_at = data.get("signed_at", "")
        checkpoint_ref = data.get("checkpoint_ref", "")
        pubkey = data.get("public_key", "")
        sig = data.get("signature", "")

        lines.extend(
            [
                f'<a id="sig-{short}"></a>',
                f"### Checkpoint: {checkpoint_ref}",
                "",
                f"**AI:** `{ai}` | **Signed:** {_format_date(signed_at)}",
                "",
                f"**Public key:** `{pubkey[:16]}...`",
                "",
                f"**Signature:** `{sig[:32]}...`",
                "",
                "---",
                "",
            ]
        )

    lines.append("*Generated by [Empirica](https://getempirica.com)*")
    return "\n".join(lines)


def _render_calibration(cal_data):
    """Render calibration.md — learning trajectory and grounded corrections."""
    lines = [
        "# Calibration",
        "",
        "> Learning trajectory (PREFLIGHT→POSTFLIGHT) and grounded bias corrections.",
        "",
    ]

    if not cal_data:
        lines.append("*No calibration data found in .breadcrumbs.yaml*")
        return "\n".join(lines)

    # Support both old 'calibration:' and new 'learning_trajectory:' section names
    cal = cal_data.get("learning_trajectory", cal_data.get("calibration", {}))
    if not cal:
        lines.append("*No learning trajectory section in .breadcrumbs.yaml*")
        return "\n".join(lines)

    ai_id = cal.get("ai_id", "?")
    observations = cal.get("observations", 0)
    last_updated = cal.get("last_updated", "")

    lines.extend(
        [
            f"**AI:** `{ai_id}` | **Observations:** {observations} | **Updated:** {_format_date(last_updated)}",
            "",
        ]
    )

    # Support both old 'bias_corrections' and new 'session_deltas' key names
    corrections = cal.get("session_deltas", cal.get("bias_corrections", {}))
    if corrections:
        lines.extend(
            [
                "## Learning Trajectory (Session Deltas)",
                "",
                "| Vector | Correction | Direction |",
                "|--------|------------|-----------|",
            ]
        )
        for k, v in sorted(corrections.items(), key=lambda x: abs(x[1]), reverse=True):
            sign = "+" if v > 0 else ""
            direction = (
                "\u2b06\ufe0f underestimates"
                if v > 0.02
                else "\u2b07\ufe0f overestimates"
                if v < -0.02
                else "\u2194\ufe0f well calibrated"
            )
            lines.append(f"| {k} | {sign}{v:.3f} | {direction} |")
        lines.append("")

    readiness = cal.get("readiness", {})
    if readiness:
        lines.extend(
            [
                "## Readiness Gate",
                "",
                f"- **min_know:** {readiness.get('min_know', '?')}",
                f"- **max_uncertainty:** {readiness.get('max_uncertainty', '?')}",
                "",
            ]
        )

    summary = cal.get("summary", {})
    if summary:
        over = summary.get("overestimates", [])
        under = summary.get("underestimates", [])
        if over:
            lines.append(f"**Overestimates:** {', '.join(over)}")
        if under:
            lines.append(f"**Underestimates:** {', '.join(under)}")
        lines.append("")

    lines.extend(["---", "*Generated by [Empirica](https://getempirica.com)*"])
    return "\n".join(lines)


def _render_readme(counts, recent_items):
    """Render root README.md — project epistemic dashboard."""
    sum(counts.values())
    lines = [
        "# Empirica Epistemic Audit Trail",
        "",
        "> Complete knowledge state of this project, tracked by [Empirica](https://getempirica.com).",
        "",
        "## Overview",
        "",
        "| Artifact | Count | Description |",
        "|----------|-------|-------------|",
        f"| \U0001f4dd [Findings](findings.md) | **{counts.get('findings', 0)}** | Validated knowledge |",
        f"| \u2753 [Unknowns](unknowns.md) | **{counts.get('unknowns', 0)}** | Open questions |",
        f"| \U0001f6ab [Dead Ends](dead-ends.md) | **{counts.get('dead_ends', 0)}** | Failed approaches |",
        f"| \u26a0\ufe0f [Mistakes](mistakes.md) | **{counts.get('mistakes', 0)}** | Errors + prevention |",
        f"| \U0001f3af [Goals](goals.md) | **{counts.get('goals', 0)}** | Work units + tasks |",
        f"| \U0001f4ca [Sessions](sessions.md) | **{counts.get('sessions', 0)}** | Session timeline |",
        f"| \U0001f9ec [Transactions](transactions.md) | **{counts.get('transactions', 0)}** | Epistemic trajectories |",
        f"| \U0001f91d [Handoffs](handoffs.md) | **{counts.get('handoffs', 0)}** | Session continuity |",
        f"| \U0001f50d [Cascades](cascades.md) | **{counts.get('cascades', 0)}** | Investigation decisions |",
        f"| \U0001f4d6 [Lessons](lessons.md) | **{counts.get('lessons', 0)}** | Procedural knowledge |",
        f"| \U0001f4ce [Sources](sources.md) | **{counts.get('sources', 0)}** | Reference documents |",
        f"| \U0001f510 [Signatures](signatures.md) | **{counts.get('signatures', 0)}** | Crypto provenance |",
        "| \U0001f3af [Calibration](calibration.md) | \u2014 | Bias corrections |",
        "",
    ]

    if recent_items:
        type_dirs = {
            "finding": ("findings.md", "finding"),
            "unknown": ("unknowns.md", "unknown"),
            "dead_end": ("dead-ends.md", "deadend"),
            "mistake": ("mistakes.md", "mistake"),
            "goal": ("goals.md", "goal"),
        }
        type_emoji = {
            "finding": "\U0001f4dd",
            "unknown": "\u2753",
            "dead_end": "\U0001f6ab",
            "mistake": "\u26a0\ufe0f",
            "goal": "\U0001f3af",
        }
        text_extractors = {
            "finding": lambda d: d.get("finding", d.get("finding_data", {}).get("finding", "")),
            "unknown": lambda d: d.get("unknown", ""),
            "dead_end": lambda d: d.get("approach", ""),
            "mistake": lambda d: d.get("mistake", ""),
            "goal": lambda d: d.get("goal_data", {}).get("objective", "") or d.get("objective", ""),
        }

        lines.extend(
            [
                "## Recent Activity",
                "",
                "| Date | Type | Summary |",
                "|------|------|---------|",
            ]
        )

        for item_type, item_id, item_data in recent_items[:20]:
            date = _format_date_short(item_data.get("created_at") or item_data.get("created_timestamp", ""))
            emoji = type_emoji.get(item_type, "\u25c8")
            short = _short_id(item_id)
            file_name, anchor_prefix = type_dirs.get(item_type, ("sessions.md", "session"))
            text = _truncate(text_extractors.get(item_type, lambda d: "")(item_data), 80)
            lines.append(f"| {date} | {emoji} | [{text}]({file_name}#{anchor_prefix}-{short}) |")

    lines.extend(
        [
            "",
            "---",
            "*Generated by [Empirica](https://getempirica.com) \u2014 epistemic infrastructure for AI-assisted work*",
        ]
    )
    return "\n".join(lines)


# ── Main Generation ──


def generate_artifacts(workspace_root, output_dir=None, verbose=False):
    """Read git notes + cold storage and generate .empirica/ markdown audit trail.

    Args:
        workspace_root: Git repository root path
        output_dir: Output directory (default: {workspace_root}/.empirica)
        verbose: Print progress

    Returns:
        dict with counts and status
    """
    workspace = str(workspace_root)
    out = Path(output_dir or os.path.join(workspace, ".empirica"))
    out.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"Reading git notes from {workspace}...")

    # Read all artifact types from git notes
    findings = _read_all_notes(workspace, "findings")
    unknowns = _read_all_notes(workspace, "unknowns")
    dead_ends = _read_all_notes(workspace, "dead_ends")
    mistakes = _read_all_notes(workspace, "mistakes")
    goals = _read_all_notes(workspace, "goals")
    handoffs = _read_all_notes(workspace, "handoff")
    signatures = _read_all_notes(workspace, "signatures")

    # Tasks — index by goal_id
    raw_tasks = _read_all_notes(workspace, "tasks")
    tasks_by_goal = {}
    for _tid, tdata in raw_tasks:
        gid = tdata.get("goal_id", "")
        tasks_by_goal.setdefault(gid, []).append(tdata)

    # Session trajectories (nested ref structure)
    sessions_map = _read_session_refs(workspace)

    # Cascades (multi-line append-only format)
    cascades = _read_cascade_refs(workspace)

    # Cold storage
    lessons = _read_lessons(workspace)
    ref_docs = _read_ref_docs(workspace)
    calibration = _read_calibration(workspace)

    if verbose:
        print(f"  Findings:     {len(findings)}")
        print(f"  Unknowns:     {len(unknowns)}")
        print(f"  Dead ends:    {len(dead_ends)}")
        print(f"  Mistakes:     {len(mistakes)}")
        print(f"  Goals:        {len(goals)}")
        print(f"  Tasks:        {len(raw_tasks)}")
        print(f"  Sessions:     {len(sessions_map)}")
        print(f"  Transactions: {sum(len(v) for v in sessions_map.values())} checkpoints")
        print(f"  Handoffs:     {len(handoffs)}")
        print(f"  Cascades:     {len(cascades)}")
        print(f"  Lessons:      {len(lessons)}")
        print(f"  Sources:      {len(ref_docs)}")
        print(f"  Signatures:   {len(signatures)}")

    # Generate all markdown files
    files = {
        "findings.md": _render_findings(findings),
        "unknowns.md": _render_unknowns(unknowns),
        "dead-ends.md": _render_dead_ends(dead_ends),
        "mistakes.md": _render_mistakes(mistakes),
        "goals.md": _render_goals(goals, tasks_by_goal),
        "transactions.md": _render_transactions(sessions_map),
        "sessions.md": _render_sessions(sessions_map),
        "handoffs.md": _render_handoffs(handoffs),
        "cascades.md": _render_cascades(cascades),
        "lessons.md": _render_lessons(lessons),
        "sources.md": _render_sources(ref_docs),
        "signatures.md": _render_signatures(signatures),
        "calibration.md": _render_calibration(calibration),
    }

    # Build recent items for dashboard
    recent_items = []
    for fid, data in findings:
        recent_items.append(("finding", fid, data))
    for uid, data in unknowns:
        recent_items.append(("unknown", uid, data))
    for did, data in dead_ends:
        recent_items.append(("dead_end", did, data))
    for mid, data in mistakes:
        recent_items.append(("mistake", mid, data))
    for gid, data in goals:
        recent_items.append(("goal", gid, data))
    recent_items.sort(key=lambda x: _get_ts(x[2]), reverse=True)

    counts = {
        "findings": len(findings),
        "unknowns": len(unknowns),
        "dead_ends": len(dead_ends),
        "mistakes": len(mistakes),
        "goals": len(goals),
        "sessions": len(sessions_map),
        "transactions": sum(len(v) for v in sessions_map.values()),
        "handoffs": len(handoffs),
        "cascades": len(cascades),
        "lessons": len(lessons),
        "sources": len(ref_docs),
        "signatures": len(signatures),
    }

    files["README.md"] = _render_readme(counts, recent_items)

    # Write all files
    for filename, content in files.items():
        (out / filename).write_text(content)

    total_files = len(files)
    if verbose:
        print(f"\nGenerated {total_files} files in {out}/")

    return {
        "ok": True,
        "output_dir": str(out),
        "counts": counts,
        "total_files": total_files,
    }


# ── CLI Handler ──


def handle_artifacts_generate_command(args):
    """CLI handler for artifacts-generate command."""
    try:
        verbose = getattr(args, "verbose", False)
        output_format = getattr(args, "output", "text")
        output_dir = getattr(args, "output_dir", None)
        bundle_format = getattr(args, "format", "audit")

        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        if result.returncode != 0:
            print("Error: Not in a git repository")
            return 1

        workspace_root = result.stdout.strip()

        # OKF format: portable Open Knowledge Format bundle (one concept per
        # artifact) instead of the browsable per-type audit trail.
        if bundle_format == "okf":
            from empirica.core.okf_export import generate_okf_bundle

            okf = generate_okf_bundle(
                workspace_root=workspace_root,
                output_dir=output_dir,
                verbose=(verbose or output_format != "json"),
            )
            if output_format == "json":
                print(json.dumps(okf, indent=2))
            else:
                print(f"\n✅ OKF bundle: {okf['concept_count']} concepts in {okf['output_dir']}/")
                for t, n in sorted(okf.get("by_type", {}).items()):
                    print(f"   {t}: {n}")
            return 0

        result = generate_artifacts(
            workspace_root=workspace_root,
            output_dir=output_dir,
            verbose=(verbose or output_format != "json"),
        )

        if output_format == "json":
            print(json.dumps(result, indent=2))
        else:
            counts = result["counts"]
            print("\n\u2705 Generated .empirica/ audit trail:")
            print(f"   \U0001f4dd Findings:      {counts['findings']}")
            print(f"   \u2753 Unknowns:      {counts['unknowns']}")
            print(f"   \U0001f6ab Dead Ends:     {counts['dead_ends']}")
            print(f"   \u26a0\ufe0f  Mistakes:      {counts['mistakes']}")
            print(f"   \U0001f3af Goals:         {counts['goals']}")
            print(f"   \U0001f4ca Sessions:      {counts['sessions']}")
            print(f"   \U0001f9ec Transactions:  {counts['transactions']} checkpoints")
            print(f"   \U0001f91d Handoffs:      {counts['handoffs']}")
            print(f"   \U0001f50d Cascades:      {counts['cascades']}")
            print(f"   \U0001f4d6 Lessons:       {counts['lessons']}")
            print(f"   \U0001f4ce Sources:       {counts['sources']}")
            print(f"   \U0001f510 Signatures:    {counts['signatures']}")
            print(f"\n   {result['total_files']} files in {result['output_dir']}")

        return 0

    except Exception as e:
        logger.error(f"Error generating artifacts: {e}")
        if getattr(args, "verbose", False):
            import traceback

            traceback.print_exc()
        print(f"Error: {e}")
        return 1

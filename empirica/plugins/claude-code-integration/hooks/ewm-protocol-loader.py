#!/usr/bin/env python3
"""
EWM Protocol Loader — SessionStart hook that reads workflow-protocol.yaml
and injects personalized behavioral configuration into session context.

Chains after session-init.py. Loads the user's workflow protocol and
configures AI behavior based on their preferences.

User identification: EMPIRICA_USER env var → prompt for identification.
Protocol search: project dir → home dir → skip gracefully.
"""

import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path

try:
    import yaml as _yaml_check  # noqa: F401 — availability check for HAS_YAML  # pyright: ignore[reportUnusedImport]

    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# Patterns that look like prompt injection in YAML values
INJECTION_PATTERNS = [
    r"(?i)ignore\s+(previous|all|above)\s+(instructions?|rules?|prompts?)",
    r"(?i)you\s+are\s+now\s+",
    r"(?i)system\s*:\s*",
    r"(?i)new\s+instructions?\s*:",
    r"(?i)override\s+(safety|security|rules)",
    r"(?i)act\s+as\s+(if|though)\s+you",
    r"(?i)disregard\s+(all|any|previous)",
    r"(?i)admin\s+(mode|override|access)",
]


def find_protocol() -> Path | None:
    """Find workflow-protocol.yaml — project dir first, then home."""
    search_paths = [
        Path.cwd() / "workflow-protocol.yaml",
        Path.cwd() / ".empirica" / "workflow-protocol.yaml",
    ]

    # Check git root if different from cwd
    try:
        import subprocess

        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            git_root = Path(result.stdout.strip())
            search_paths.insert(0, git_root / "workflow-protocol.yaml")
            search_paths.insert(1, git_root / ".empirica" / "workflow-protocol.yaml")
    except Exception:
        pass

    # Also check home-level for global protocol
    search_paths.extend(
        [
            Path.home() / ".empirica" / "workflow-protocol.yaml",
            Path.home() / "workflow-protocol.yaml",
        ]
    )

    # Deduplicate while preserving order
    seen = set()
    for p in search_paths:
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            if resolved.exists():
                return resolved

    return None


def check_file_permissions(path: Path) -> list[str]:
    """Check file permissions — warn if too open."""
    warnings = []
    try:
        file_stat = path.stat()
        mode = file_stat.st_mode

        # Check if group or others can read
        if mode & stat.S_IRGRP:
            warnings.append(f"Protocol readable by group (mode: {oct(mode)})")
        if mode & stat.S_IROTH:
            warnings.append(f"Protocol readable by others (mode: {oct(mode)})")

        # Check if anyone other than owner can write
        if mode & (stat.S_IWGRP | stat.S_IWOTH):
            warnings.append(f"SECURITY: Protocol writable by non-owner (mode: {oct(mode)})")

    except OSError:
        warnings.append("Could not check file permissions")

    return warnings


def verify_integrity(path: Path) -> tuple[bool, str]:
    """Verify protocol integrity via SHA256 hash file."""
    hash_path = path.with_suffix(".yaml.sha256")

    # Compute current hash
    with open(path, "rb") as f:
        current_hash = hashlib.sha256(f.read()).hexdigest()

    if not hash_path.exists():
        # No hash file — create one (first run)
        try:
            hash_path.write_text(current_hash)
            return True, "integrity_baseline_created"
        except OSError:
            return True, "no_hash_file"

    stored_hash = hash_path.read_text().strip()
    if current_hash == stored_hash:
        return True, "integrity_verified"
    else:
        return False, f"INTEGRITY MISMATCH: stored={stored_hash[:12]}... current={current_hash[:12]}..."


def sanitize_value(value: str) -> str:
    """Strip potential prompt injection patterns from string values."""
    if not isinstance(value, str):
        return value

    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, value):
            return "[SANITIZED: suspicious pattern detected in value]"

    return value


def sanitize_protocol(data: dict) -> tuple[dict, list[str]]:
    """Recursively sanitize all string values in the protocol. Returns sanitized data and warnings."""
    warnings = []

    def _sanitize(obj, path=""):
        if isinstance(obj, str):
            sanitized = sanitize_value(obj)
            if sanitized != obj:
                warnings.append(f"Sanitized value at {path}: injection pattern detected")
            return sanitized
        elif isinstance(obj, dict):
            return {k: _sanitize(v, f"{path}.{k}") for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_sanitize(v, f"{path}[{i}]") for i, v in enumerate(obj)]
        return obj

    sanitized = _sanitize(data)
    if not isinstance(sanitized, dict):  # Shouldn't happen — data is dict, so _sanitize returns dict
        sanitized = {}
    return sanitized, warnings


def load_protocol(path: Path) -> dict | None:
    """Load workflow protocol YAML."""
    if HAS_YAML:
        import yaml as _yaml

        with open(path) as f:
            return _yaml.safe_load(f)
    else:
        # Fallback: basic YAML parsing for simple structures
        # Won't handle nested structures well, but enough for detection
        with open(path) as f:
            content = f.read()
        return {"_raw": content, "_parse_method": "raw"}


def get_current_user() -> str | None:
    """Determine current user from env or context."""
    # Explicit env var
    user = os.getenv("EMPIRICA_USER")
    if user:
        return user.lower().strip()

    # Check for user marker file
    marker = Path.home() / ".empirica" / "current_user"
    if marker.exists():
        return marker.read_text().strip().lower()

    return None


def format_user_preferences(protocol: dict, user_name: str) -> str:
    """Extract and format preferences for a specific user."""
    parts = []

    # Find user in team
    team = protocol.get("team", [])
    user_info = None
    for member in team:
        if member.get("name", "").lower() == user_name:
            user_info = member
            break

    if user_info:
        parts.append(f"**User:** {user_info.get('name', user_name)}")
        parts.append(f"**Role:** {user_info.get('role', 'team member')}")
        parts.append(f"**Focus:** {user_info.get('focus', 'general')}")
    else:
        parts.append(f"**User:** {user_name}")

    # Work preferences
    prefs = protocol.get("work_preferences", {}).get(user_name, {})
    if prefs:
        parts.append("")
        parts.append("### Behavioral Configuration")

        autonomy = prefs.get("ai_autonomy_level", "collaborative")
        uncertainty = prefs.get("uncertainty_surfacing", "when_material")
        pushback = prefs.get("pushback_style", "direct_and_factual")
        calibration = prefs.get("calibration_mode", "single_pass")

        autonomy_desc = {
            "collaborative_equal_partner": "Equal partner — check in on approach before acting, multi-round calibration",
            "full_autonomy": "Full autonomy — act decisively, surface uncertainty explicitly, maximize efficiency",
            "ai_first_efficiency_maximizing": "AI-first — lead with action, check in only when material",
            "collaborative_with_checkpoints": "Collaborative — act between checkpoints, verify at gates",
            "assistant_mode": "Assistant — wait for direction, execute precisely",
        }.get(autonomy, autonomy)

        uncertainty_desc = {
            "empirica_managed": "Empirica-managed — use measured vectors, not vibes",
            "always_explicit": "Always surface uncertainty explicitly in natural language",
            "when_material": "Surface only when it materially affects the decision",
            "minimal": "Minimal — only flag critical gaps",
        }.get(uncertainty, uncertainty)

        pushback_desc = {
            "direct_and_factual": "Direct and factual — no hedging, no beating around the bush",
            "gentle_reframe": "Gentle reframe — redirect without confrontation",
            "socratic": "Socratic — ask questions until they see it themselves",
        }.get(pushback, pushback)

        parts.append(f"- **Autonomy:** {autonomy_desc}")
        parts.append(f"- **Uncertainty:** {uncertainty_desc}")
        parts.append(f"- **Pushback:** {pushback_desc}")
        parts.append(f"- **Calibration:** {calibration}")

        # Task splitting
        splitting = prefs.get("task_splitting", {})
        if splitting.get("ai_autonomous"):
            parts.append(f"- **AI autonomous:** {', '.join(splitting['ai_autonomous'])}")
        if splitting.get("ai_with_checkpoint"):
            parts.append(f"- **Needs checkpoint:** {', '.join(splitting['ai_with_checkpoint'])}")
        if splitting.get("human_only"):
            parts.append(f"- **Human only:** {', '.join(splitting['human_only'])}")

    # Domains
    domains = protocol.get("domains", {}).get(user_name, {})
    if domains:
        parts.append("")
        parts.append("### Domain Expertise")
        if domains.get("expert"):
            parts.append(f"- **Expert in:** {', '.join(domains['expert'])}")
        if domains.get("learning"):
            parts.append(f"- **Learning:** {', '.join(domains['learning'])}")
        if domains.get("novice"):
            parts.append(f"- **Novice in:** {', '.join(domains['novice'])}")

    return "\n".join(parts)


def format_shared_context(protocol: dict) -> str:
    """Format team-wide context (goals, AAP, trust, non-negotiables)."""
    parts = []

    # Goals summary
    goals = protocol.get("goals", {})
    primary = goals.get("primary", [])
    if primary:
        parts.append("### Active Goals")
        for g in primary[:4]:
            desc = g.get("description", str(g)) if isinstance(g, dict) else str(g)
            parts.append(f"- {desc}")

    # AAP
    aap = protocol.get("anti_agreement_protocol", {})
    if aap.get("enabled"):
        parts.append("")
        parts.append("### Anti-Agreement Protocol: ACTIVE")
        parts.append(f"- Mode: **{aap.get('mode', 'direct')}**")
        regulation = aap.get("ai_self_regulation", {})
        rules = []
        if regulation.get("no_ungrounded_agreement"):
            rules.append("never agree without grounding")
        if regulation.get("no_hedge_mirroring"):
            rules.append("never mirror hedged language")
        if regulation.get("quantify_confidence"):
            rules.append("quantify confidence when relevant")
        if regulation.get("name_uncertainties"):
            rules.append("name uncertainties explicitly")
        if rules:
            parts.append(f"- Rules: {', '.join(rules)}")

    # Non-negotiables
    trust = protocol.get("trust_building", {})
    non_neg = trust.get("non_negotiables", [])
    if non_neg:
        parts.append("")
        parts.append("### Non-Negotiables")
        for nn in non_neg:
            if not nn.startswith("#"):  # Skip YAML comments
                parts.append(f"- {nn}")

    return "\n".join(parts)


def format_unknown_user_prompt(protocol: dict) -> str:
    """Prompt to identify the user when not auto-detected."""
    team = protocol.get("team", [])
    if not team:
        return ""

    names = [m.get("name", "?") for m in team]
    parts = [
        "### User Identification Required",
        "",
        "EWM protocol loaded but user not identified.",
        f"Team members: **{', '.join(names)}**",
        "",
        "Ask who is speaking to load their behavioral profile.",
        "Set `EMPIRICA_USER` env var or create `~/.empirica/current_user` to auto-detect.",
    ]
    return "\n".join(parts)


def _resolve_ai_id_for_poll() -> str | None:
    """This practice's ai_id from .empirica/project.yaml (git root or cwd).

    Prefer the basename ``ai_id`` — the mailbox-poll CLI resolves it to the
    canonical 3-form internally; fall back to ``canonical_seat``. Returns None
    when there's no project.yaml or no id, so the caller skips the inbox lead.
    """
    if not HAS_YAML:
        return None
    import yaml

    candidates = [Path.cwd() / ".empirica" / "project.yaml"]
    try:
        import subprocess

        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            candidates.insert(0, Path(r.stdout.strip()) / ".empirica" / "project.yaml")
    except Exception:
        pass
    for p in candidates:
        try:
            if not p.exists():
                continue
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            ai_id = data.get("ai_id") or data.get("canonical_seat")
            if ai_id:
                return str(ai_id)
        except Exception:
            continue
    return None


def _build_pending_inbox_lead() -> str:
    """Render a 'handle these FIRST' lead block of pending mesh inbox messages.

    The SessionStart greet-prime (the EWM block below) dominates injected wake
    context so hard that models greet + cite the user's goals instead of
    reacting to a pending mesh message — even when the wake CONTENT was inlined
    (verified across GLM-5.2 / Kimi-K2.6 / MiniMax-M2.7). Leading with the inbox
    — prepended BEFORE the EWM block — puts the pending messages first in the
    session's context so they aren't buried under the greeting.

    Best-effort: a bounded 6s poll (well inside the hook's 10s allowFailure
    budget, shared with find_protocol's git call). Fails open to "" on missing
    CLI / timeout / bad JSON / empty inbox / any error — never delays or breaks
    SessionStart.
    """
    ai_id = _resolve_ai_id_for_poll()
    if not ai_id:
        return ""
    try:
        import subprocess

        r = subprocess.run(
            ["empirica", "mailbox", "poll", "--ai-id", ai_id, "--status", "accepted", "--output", "json"],
            capture_output=True,
            text=True,
            timeout=6,
            check=False,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return ""
        proposals = (json.loads(r.stdout) or {}).get("proposals") or []
    except Exception:
        return ""
    if not proposals:
        return ""

    total = len(proposals)
    shown = proposals[:8]
    lines = [f"## 📬 Pending mesh messages ({total}) — handle these FIRST", ""]
    for p in shown:
        pid = str(p.get("id") or "")[:26]
        src = p.get("source_claude") or "?"
        status = p.get("status") or "?"
        ptype = p.get("type") or "?"
        title = (p.get("title") or "").strip()
        summary = " ".join((p.get("summary") or "").split())
        if len(summary) > 300:
            summary = summary[:300] + "…"
        lines.append(f"- **[{pid}]** from `{src}` ({status}, {ptype}) — {title}")
        if summary:
            lines.append(f"  {summary}")
    if total > len(shown):
        lines.append("")
        lines.append(
            f"…and {total - len(shown)} more — run `empirica mailbox poll --status accepted` for the full list."
        )
    lines.append("")
    lines.append(
        "React per the mailbox protocol (`empirica mailbox show <id>`, "
        "`empirica mailbox reply`) before orienting or asking what to work on."
    )
    lines.append("")
    return "\n".join(lines)


def main():
    try:
        json.loads(sys.stdin.read())
    except Exception:
        pass

    # Find and load protocol
    protocol_path = find_protocol()

    if not protocol_path:
        # No protocol found — silent exit, EWM not configured
        output = {
            "ok": True,
            "ewm_active": False,
            "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ""},
        }
        print(json.dumps(output))
        sys.exit(0)

    # --- Security checks ---
    security_warnings = []

    # 1. File permissions
    perm_warnings = check_file_permissions(protocol_path)
    security_warnings.extend(perm_warnings)

    # 2. Integrity verification
    integrity_ok, integrity_status = verify_integrity(protocol_path)
    if not integrity_ok:
        security_warnings.append(integrity_status)

    # --- Load and sanitize ---
    protocol = load_protocol(protocol_path)
    if not protocol or "_raw" in protocol:
        output = {
            "ok": True,
            "ewm_active": False,
            "error": "PyYAML not installed or parse failed",
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": f"\n## EWM Protocol Found\n\nProtocol at `{protocol_path}` but PyYAML not available. Install: `pip install pyyaml`\n",
            },
        }
        print(json.dumps(output))
        sys.exit(0)

    # 3. Sanitize all string values
    protocol, sanitize_warnings = sanitize_protocol(protocol)
    security_warnings.extend(sanitize_warnings)

    # Identify user
    current_user = get_current_user()

    # Build context injection. LEAD with the pending mesh inbox (prop_pu4xrog):
    # the greet-prime below otherwise dominates so hard the model never checks
    # the inbox, so pending peer messages get ignored even when inlined. The
    # inbox block is best-effort and prepends only when non-empty.
    context_parts: list[str] = []
    inbox_lead = _build_pending_inbox_lead()
    if inbox_lead:
        context_parts.append(inbox_lead)
    context_parts.extend(["## EWM Protocol Active", ""])
    context_parts.append(f"*Protocol: `{protocol_path}`*")

    # Surface security warnings if any
    if security_warnings:
        context_parts.append("")
        context_parts.append("### Security Warnings")
        for w in security_warnings:
            context_parts.append(f"- {w}")
        context_parts.append("")
        if not integrity_ok:
            context_parts.append("**INTEGRITY CHECK FAILED.** Protocol may have been tampered with.")
            context_parts.append("Run `/ewm-interview` to regenerate or verify manually.")
            context_parts.append("")
    else:
        context_parts.append(f" | Integrity: {integrity_status}")

    context_parts.append("")

    if current_user:
        # Known user — load their preferences
        user_context = format_user_preferences(protocol, current_user)
        context_parts.append(user_context)
    else:
        # Unknown user — ask for identification
        id_prompt = format_unknown_user_prompt(protocol)
        context_parts.append(id_prompt)

    # Always include shared context
    shared = format_shared_context(protocol)
    if shared:
        context_parts.append("")
        context_parts.append(shared)

    # Ecosystem vision (if present)
    ecosystem = protocol.get("ecosystem_vision", {})
    if ecosystem.get("architecture"):
        context_parts.append("")
        context_parts.append("### Ecosystem Vision")
        context_parts.append(f"*{ecosystem['architecture']}*")

    full_context = "\n".join(context_parts)

    output = {
        "ok": True,
        "ewm_active": True,
        "protocol_path": str(protocol_path),
        "user_identified": current_user is not None,
        "current_user": current_user,
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": full_context},
    }

    # Stderr for user-visible message
    user_label = current_user.capitalize() if current_user else "unidentified"
    print(f"EWM: Protocol loaded ({user_label}) from {protocol_path.name}", file=sys.stderr)

    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()

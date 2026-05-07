#!/usr/bin/env python3
"""
Epistemic Tool Router - UserPromptSubmit Hook

Runs on every user prompt BEFORE Claude starts reasoning. Assesses the task
against the current epistemic state and recommends specific Empirica
tools/agents/skills, influencing Claude's tool selection.

This is the bridge between "what should I do" (VectorRouter modes) and
"what should I use" (specific agents, skills, MCP tools).

Input (stdin JSON):
  {"prompt": "user's prompt text"}

Output (stdout JSON):
  {"continue": true, "context": "routing advice text"}

Performance target: < 2 seconds (runs on every prompt).
"""

import json
import sys
import time
from pathlib import Path

# ============================================================================
# Agent domain registry — keyword → agent mapping
# ============================================================================

AGENT_DOMAINS = {
    "empirica:security": {
        "keywords": [
            "security", "auth", "authentication", "authorization",
            "encrypt", "vulnerability", "xss", "csrf", "injection",
            "token", "credential", "permission", "access control",
            "threat", "attack", "sanitiz",
        ],
        "description": "Security analysis and hardening",
    },
    "empirica:architecture": {
        "keywords": [
            "architecture", "design pattern", "refactor",
            "modular", "coupling", "cohesion", "abstraction",
            "interface", "dependency", "scalab", "structure",
            "component", "layer", "separation of concerns",
            "system design",
        ],
        "description": "Architecture analysis and system design",
    },
    "empirica:performance": {
        "keywords": [
            "performance", "optimiz", "latency", "throughput",
            "memory", "cpu", "cache", "profil", "slow",
            "bottleneck", "n+1", "query optim", "index",
        ],
        "description": "Performance analysis and optimization",
    },
    "empirica:ux": {
        "keywords": [
            "usability", "accessibility", "user flow", "ux",
            "error message", "response time", "wcag", "a11y",
            "user experience", "interaction design",
        ],
        "description": "UX and accessibility analysis",
    },
}

# ============================================================================
# AAP (Anti-Agreement Protocol) — Hedge detection patterns
# ============================================================================

# Hedge patterns with categories and deobfuscation prompts
HEDGE_PATTERNS = {
    "softening_qualifiers": {
        "patterns": [
            r'\bkind of\b', r'\bsort of\b', r'\bmaybe\b', r'\bperhaps\b',
            r'\bI guess\b', r'\bI suppose\b', r'\bprobably\b',
            r'\bmight be\b', r'\bcould be\b',
        ],
        "deobfuscation": "You used softening language — what's the specific thing you mean?",
    },
    "dismissive_agreement": {
        "patterns": [
            r'\byeah\s+(sure|fine|ok|whatever)\b',
            r'\bI hear you\b', r'\bfair enough\b', r'\bif you say so\b',
            r'\bI\'m not going to argue\b', r'\blet\'s just go with\b',
        ],
        "deobfuscation": "That sounded like agreement without conviction — do you actually agree, or is there a reservation?",
    },
    "vague_deflection": {
        "patterns": [
            r'\bit\'s complicated\b', r'\bit depends\b', r'\bnot really\b',
            r'\bnot exactly\b', r'\bnot wrong\b', r'\bnot necessarily\b',
            r'\bin a way\b', r'\bto some extent\b',
        ],
        "deobfuscation": "Can you be more specific? What exactly is complicated / what does it depend on?",
    },
    "passive_uncertainty": {
        "patterns": [
            r'\bI\'?m not sure\s+(if|whether|about|that)\b',
            r'\bI don\'?t know\s+(if|whether|about)\b',
            r'\bI\'?m not (really\s+)?sure\b',
        ],
        "deobfuscation": "What specifically are you unsure about? Can you name the uncertainty?",
    },
    "false_modesty": {
        "patterns": [
            r'\bI\'?m (probably|just)\s+(wrong|being|overthinking)\b',
            r'\bthis is (probably\s+)?(stupid|dumb|obvious)\b',
            r'\byou (probably\s+)?know better\b',
            r'\bI\'?m no expert\b',
        ],
        "deobfuscation": "Don't discount your own assessment — what's the actual concern you're raising?",
    },
}

# ============================================================================
# EPP Semantic Pushback Check — always-on forcing block for substantive prompts
# ============================================================================
# See: docs/superpowers/specs/2026-04-07-epp-strengthening-design.md
# Phase 0 experiment (2026-04-07) validated effect size across Opus/Sonnet/Haiku:
# all 3 models passed the decision gate (>=20% improvement on >=2/6 metrics).
# Block is injected LAST in additionalContext to exploit attention recency bias.

SEMANTIC_PUSHBACK_CHECK_BLOCK = """<semantic-pushback-check>
Before generating your response:

1. Did your prior turn contain a substantive claim — assertion, recommendation,
   analysis, position?

2. Does the user's current message push back on that claim in ANY form:
   direct contradiction, evidential challenge, soft doubt, scope shift,
   rhetorical reframe, paraphrase-level disagreement, implicit challenge,
   request for justification?

3. If YES to both → you are in an EPP situation. Before responding:
   ANCHOR (recall claim + confidence + basis)
   → CLASSIFY (EMOTIONAL / RHETORICAL / EVIDENTIAL / LOGICAL / CONTEXTUAL)
   → DECIDE (HOLD / SOFTEN / UPDATE / REFRAME)
   → RESPOND with audit trail — never silently shift, never apologise for
   holding ground when pushback is non-evidential.

4. If NO to either → proceed normally. This check must be semantic, not
   keyword-based. You are the classifier.
</semantic-pushback-check>"""

# Minimum prompt length to inject the semantic-check block.
# Filters out trivial inputs like "ok", "yes", "continue" where EPP is
# irrelevant. Matches the same threshold used by epistemic routing.
SEMANTIC_CHECK_MIN_LENGTH = 20


def build_semantic_pushback_check(prompt: str) -> str | None:
    """Return the semantic-check block for substantive prompts, None otherwise.

    The block is returned for any user message that is long enough to
    plausibly contain pushback on a prior substantive claim, and does NOT
    start with a slash command (which has its own handling).

    The actual pushback detection happens in Claude's generation step —
    the block instructs Claude to do a semantic self-check as its first
    generation step. This respects the LLM/software distinction: regex
    matching on speech acts is brittle, but LLMs handle paraphrase,
    irony, and implicit challenge natively.
    """
    if len(prompt) < SEMANTIC_CHECK_MIN_LENGTH:
        return None
    if prompt.startswith("/"):
        return None
    return SEMANTIC_PUSHBACK_CHECK_BLOCK


# ============================================================================
# End EPP block
# ============================================================================


# ============================================================================
# Investigation Depth Proportionality (Tx-AB)
# ============================================================================
#
# Symptom this block addresses: agent runs 30+ file reads on a question where
# the user already supplied the hypothesis ("might need to create a session",
# "I think it's the X path", "probably the config"). Reading 30 files looks
# diligent but burns context and delays the actual hypothesis-test. The
# proportional response is one targeted probe.
#
# Diagnosis case 2026-05-06: Kimi/Sonnet/Opus all show this pattern in
# ecodex; default-mode bias from upstream training treats every prompt as
# deep-research even when the user's framing is "quick check, I think X".
#
# Trigger detection is regex-based on hypothesis markers + scope-shaping
# verbs. The block doesn't disable investigation — it asks the agent to
# size the probe to the hypothesis BEFORE branching wider.

INVESTIGATION_PROPORTIONALITY_BLOCK = """<investigation-proportionality>
**STOP.** The user's prompt contains a hypothesis marker or
proportional-scope cue ("I think...", "maybe...", "might need...",
"check on...", "verify", "quick look").

**Your FIRST action MUST be the smallest disconfirming probe** — a
single bash / grep / read that would prove the user's hypothesis wrong
if wrong, or confirm a key prediction if right. Not a survey. Not a
mental model. One probe.

Required sequence:
1. **NAME the hypothesis** in your reply (one sentence: "Hypothesis: X").
2. **RUN the disconfirming probe** as your next tool call.
3. ONLY IF the probe disconfirms or surfaces a new question, expand
   investigation. Otherwise, answer.

**Hard rule:** the Sentinel firewall is now armed. After 5 read/grep/glob
tool calls without naming a hypothesis and running a probe, further
investigation tools will be DENIED with the same reasoning. This is
not a soft suggestion — it's a runtime constraint. Survey-mode is
explicitly blocked here because the user already gave you the
hypothesis to test.

Anti-patterns this block kills:
- Reading the entire subsystem to verify a one-line config change.
- Running grep across the codebase before checking if the answer is in
  the user's own message.
- Building a mental model of code you haven't touched, when one command
  would tell you which path to look at.

This is NOT a ban on thorough work — depth is fine AFTER the probe.
The discipline is "test first, expand on evidence", not "skim and
assume". When you genuinely need to map an unfamiliar subsystem, the
prompt won't trigger this block. When the user hands you a hypothesis,
test it.
</investigation-proportionality>"""


# Hypothesis markers — phrases that signal the user has a working theory.
# Detection is regex-based + lower-cased + word-boundary anchored to avoid
# false positives like "thinking about" or "check this code". We catch the
# common framings that came up in real usage 2026-05-06.
PROPORTIONALITY_HYPOTHESIS_PATTERNS = [
    r"\bi think\b",
    r"\bi suspect\b",
    r"\bi believe\b",
    r"\bmaybe\b",
    r"\bmight (be|need|have|require)\b",
    r"\bprobably\b",
    r"\blikely\b",
    r"\bI'?d guess\b",
    r"\bmy hunch\b",
    r"\bsuspect (it'?s|the)\b",
    r"\bcould be\b",
]

# Proportional-scope phrasing — "test the hypothesis with a small probe"
# rather than "do a full audit". Same regex shape as above.
PROPORTIONALITY_SCOPE_PATTERNS = [
    r"\bquick (check|look|test|peek|sanity)\b",
    r"\bjust (check|verify|confirm|look)\b",
    r"\bsanity check\b",
    r"\bsmoke test\b",
    r"\bcheck on (this|that|the)\b",
    r"\bjust to (verify|confirm)\b",
    r"\bcan you (check|verify|confirm)\b",
]

# Min length filters trivial inputs ("ok", "yes", "continue") but stays low
# enough that genuine probe prompts ("sanity check please", "quick check"
# alone) still match. EPP threshold is 20 because pushback semantics need
# longer context; proportionality cues are usually shorter.
PROPORTIONALITY_MIN_LENGTH = 12


def _proportionality_state_path(session_id: str) -> Path:
    """Where the Sentinel-side investigation budget counter lives.

    Keyed by codex/claude session_id so multiple concurrent sessions
    don't share a budget. Lives under ~/.empirica/state/ (pre-existing
    transient-state dir; sentinel-gate writes other state files here).
    """
    safe_sid = "".join(c if c.isalnum() or c in "-_" else "_" for c in (session_id or "no-sid"))
    return Path.home() / ".empirica" / "state" / f"proportionality_{safe_sid}.json"


def _arm_proportionality_budget(session_id: str, limit: int = 5) -> None:
    """Tx-AG: arm the read/grep/glob budget so sentinel-gate can deny
    investigation-as-procrastination after `limit` tool calls.

    Called from the UserPromptSubmit handler when the proportionality
    block fires. Sentinel-gate reads this file in PreToolUse to track
    counts. State decays naturally — overwritten on each new
    hypothesis-bearing prompt; stale files (>1h old) are ignored by
    the reader. Fail-quiet: if state dir isn't writable, tool-router
    just emits the soft-block context and continues.
    """
    if not session_id:
        return
    try:
        path = _proportionality_state_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "armed_at": time.time(),
            "tool_count": 0,
            "limit": limit,
            "session_id": session_id,
        }
        path.write_text(json.dumps(payload))
    except OSError:
        # Hard fail-quiet: budget arming is best-effort. If the disk is
        # full or the state dir is unwritable, the soft-block context
        # already shipped above is the fallback.
        pass


def build_investigation_proportionality_check(prompt: str) -> str | None:
    """Return the proportionality block for prompts containing hypothesis or
    quick-scope markers, None otherwise.

    Detection is intentionally regex + word-boundary on a small curated list,
    NOT semantic. Goal: catch the obvious cases without false-positiving on
    every prompt. The block itself acknowledges nuance ("This is NOT a ban on
    thorough work") so a false positive only adds 10 lines of context — same
    cost-of-being-wrong as the existing EPP semantic-pushback block.
    """
    import re as _re

    if len(prompt) < PROPORTIONALITY_MIN_LENGTH:
        return None
    if prompt.startswith("/"):
        return None
    lowered = prompt.lower()
    has_marker = any(
        _re.search(pat, lowered) for pat in PROPORTIONALITY_HYPOTHESIS_PATTERNS
    ) or any(
        _re.search(pat, lowered) for pat in PROPORTIONALITY_SCOPE_PATTERNS
    )
    if not has_marker:
        return None
    return INVESTIGATION_PROPORTIONALITY_BLOCK


# ============================================================================
# End Investigation Depth Proportionality block
# ============================================================================


# Patterns that indicate genuine epistemic humility (NOT hedging)
# These should NOT trigger AAP
GENUINE_HUMILITY_PATTERNS = [
    r'\bI\'?m uncertain about .+ because\b',
    r'\bmy confidence is (low|around|about)\b',
    r'\bI don\'?t have (evidence|data|enough info)\b',
    r'\bthe evidence (suggests|shows|indicates)\b',
    r'\bbased on what I\'?ve (seen|read|found)\b',
    r'\bI need to (check|verify|investigate)\b',
]


def detect_hedges(text: str) -> list[dict]:
    """Detect hedge patterns in user text. Returns list of detected hedges with categories."""
    import re

    text_lower = text.lower()

    # Check for genuine epistemic humility first — if present, reduce sensitivity
    genuine_count = sum(
        1 for pattern in GENUINE_HUMILITY_PATTERNS
        if re.search(pattern, text_lower, re.IGNORECASE)
    )

    # If the text shows genuine epistemic reasoning, skip hedge detection
    if genuine_count >= 2:
        return []

    detected = []
    for category, config in HEDGE_PATTERNS.items():
        for pattern in config["patterns"]:
            if re.search(pattern, text_lower, re.IGNORECASE):
                detected.append({
                    "category": category,
                    "deobfuscation": config["deobfuscation"],
                })
                break  # One match per category is enough

    return detected


def load_aap_config() -> dict:
    """Load AAP configuration from workflow protocol."""
    try:
        import yaml as _yaml
        protocol_path = Path.home() / '.empirica' / 'workflow-protocol.yaml'
        if protocol_path.exists():
            with open(protocol_path) as f:
                protocol = _yaml.safe_load(f)
            return protocol.get('anti_agreement_protocol', {})
    except Exception:
        pass
    return {"enabled": False}


# Keywords that suggest investigation/exploration tasks
# (where Empirica agents are most valuable vs built-in Explore)
INVESTIGATION_KEYWORDS = [
    "investigate", "explore", "understand", "analyze", "assess",
    "audit", "review", "examine", "check", "inspect", "evaluate",
    "figure out", "look into", "dig into", "deep dive",
]

# Keywords that suggest epistemic workflow
EPISTEMIC_KEYWORDS = [
    "preflight", "postflight", "check", "cascade",
    "epistemic", "vector", "calibrat", "drift",
    "knowledge state", "confidence",
]


def get_active_session_vectors():
    """Get current session's epistemic vectors from DB. Fast path."""
    try:
        # Find active session ID using canonical instance resolution
        _lib_path = Path(__file__).parent.parent / 'lib'
        if str(_lib_path) not in sys.path:
            sys.path.insert(0, str(_lib_path))
        from project_resolver import _get_instance_suffix
        suffix = _get_instance_suffix()

        session_id = None
        for base in [Path.cwd() / '.empirica', Path.home() / '.empirica']:
            active_file = base / f'active_session{suffix}'
            if active_file.exists():
                content = active_file.read_text().strip()
                if content:
                    # Parse JSON format (CLI/MCP) or plain text (legacy)
                    if content.startswith('{'):
                        try:
                            data = json.loads(content)
                            session_id = data.get('session_id')
                        except json.JSONDecodeError:
                            session_id = content  # Fallback to raw content
                    else:
                        session_id = content  # Plain text format
                    if session_id:
                        break

        if not session_id:
            return None, None

        # Read vectors from DB
        sys.path.insert(0, str(Path.home() / 'empirical-ai' / 'empirica'))
        from empirica.data.session_database import SessionDatabase
        db = SessionDatabase()
        cursor = db.conn.cursor()

        # Get latest epistemic assessment for this session
        cursor.execute("""
            SELECT vectors FROM epistemic_assessments
            WHERE session_id = ?
            ORDER BY created_timestamp DESC LIMIT 1
        """, (session_id,))
        row = cursor.fetchone()
        db.close()

        if row:
            vectors = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            return session_id, vectors

        return session_id, None
    except Exception:
        return None, None


def determine_mode(vectors):
    """Lightweight mode determination from vectors."""
    if not vectors:
        return "unknown"

    clarity = vectors.get("clarity", 0.5)
    context = vectors.get("context", 0.5)
    uncertainty = vectors.get("uncertainty", 0.5)
    know = vectors.get("know", 0.5)

    if clarity < 0.5:
        return "clarify"
    if context < 0.5:
        return "load_context"
    if uncertainty > 0.6:
        return "investigate"
    if know >= 0.7 and uncertainty < 0.4:
        return "confident_implementation"
    return "cautious_implementation"


def match_agents(task_lower):
    """Match task keywords to domain-specific agents."""
    matches = []
    for agent_name, config in AGENT_DOMAINS.items():
        keyword_hits = sum(
            1 for kw in config["keywords"]
            if kw in task_lower
        )
        if keyword_hits > 0:
            confidence = min(0.95, 0.5 + keyword_hits * 0.15)
            matches.append({
                "name": agent_name,
                "confidence": confidence,
                "description": config["description"],
                "hits": keyword_hits,
            })
    # Sort by confidence descending
    return sorted(matches, key=lambda m: -m["confidence"])


def is_investigation_task(task_lower):
    """Check if the task suggests investigation/exploration."""
    return any(kw in task_lower for kw in INVESTIGATION_KEYWORDS)


def is_epistemic_task(task_lower):
    """Check if the task involves epistemic workflow."""
    return any(kw in task_lower for kw in EPISTEMIC_KEYWORDS)


def is_blindspot_relevant(task_lower, mode, vectors):
    """Check if blindspot scanning would be valuable for this task."""
    # Explicit blindspot keywords
    if any(kw in task_lower for kw in ["blindspot", "blind spot", "unknown unknown",
                                        "what am i missing", "what are we missing",
                                        "what might i be missing", "negative space",
                                        "coverage gap", "gap in", "gaps in"]):
        return True
    # High uncertainty + investigation mode
    if vectors and vectors.get("uncertainty", 0) > 0.5 and mode in ("investigate", "clarify"):
        return True
    # Starting new work (low completion, low context)
    return bool(
        vectors
        and vectors.get("completion", 0) < 0.15
        and vectors.get("context", 0) < 0.5
    )


# ============================================================================
# Routing advice — single-purpose helpers
# ============================================================================
# Each helper returns a list[str] of advice lines (or empty list if not
# applicable). build_routing_advice() orchestrates by calling each and
# concatenating non-empty results. Refactored from the prior monolithic
# function (CC=18) to keep each branch independently testable and below
# complexity threshold.

def _agent_match_advice(agent_matches: list) -> list[str]:
    """Advice lines recommending the top-matched domain agent(s)."""
    if not agent_matches:
        return []
    top = agent_matches[0]
    lines = [
        f"For this task, consider using the `{top['name']}` agent "
        f"({top['description']})."
    ]
    if len(agent_matches) > 1:
        others = ", ".join(f"`{m['name']}`" for m in agent_matches[1:3])
        lines.append(f"Also relevant: {others}.")
    return lines


def _investigation_routing_advice(task_lower: str, has_agent_match: bool) -> list[str]:
    """Suggest Empirica investigation tooling for exploration tasks without a domain match."""
    if not is_investigation_task(task_lower) or has_agent_match:
        return []
    return [
        "This looks like an investigation task. "
        "Use `mcp__empirica__investigate` for systematic investigation "
        "with epistemic tracking, or spawn a domain-specific agent "
        "(empirica:architecture, security, performance, ux) "
        "for focused analysis."
    ]


def _blindspot_advice(task_lower: str, mode: str, vectors) -> list[str]:
    """Suggest blindspot scanning when negative-space analysis is valuable."""
    if not is_blindspot_relevant(task_lower, mode, vectors):
        return []
    return [
        "Consider running `mcp__empirica__blindspot_scan` to detect "
        "knowledge gaps from negative space analysis before proceeding."
    ]


def _mode_based_advice(
    mode: str,
    vectors,
    task_lower: str,
    has_agent_match: bool,
) -> list[str]:
    """Mode-conditioned tool suggestions based on the current epistemic state."""
    if not vectors:
        return []
    if mode == "load_context":
        return [
            "Context is low — run `mcp__empirica__project_bootstrap` "
            "to load project context before proceeding."
        ]
    if mode == "investigate" and not has_agent_match:
        return [
            "Uncertainty is high — use `mcp__empirica__investigate` "
            "or spawn a domain agent for systematic investigation."
        ]
    if mode == "cautious_implementation" and any(
        kw in task_lower for kw in ["try", "attempt", "approach", "workaround", "fix"]
    ):
        return [
            "If this approach doesn't work, log it with "
            "`mcp__empirica__deadend_log` to prevent re-exploration."
        ]
    return []


def _epistemic_workflow_advice(task_lower: str) -> list[str]:
    """Hint at the Empirica epistemic workflow for transaction-related tasks."""
    if not is_epistemic_task(task_lower):
        return []
    return [
        "This involves epistemic workflow. "
        "Use the Empirica MCP tools (preflight/check/postflight) "
        "or invoke the `epistemic-transaction` skill for planning guidance."
    ]


def build_routing_advice(task, vectors, _session_id=None):
    """Build routing advice from task + vectors.

    Orchestrates 5 single-purpose advice helpers and concatenates their
    non-empty outputs. Returns None if no helper produced any advice.
    `_session_id` is accepted for backward compatibility but unused —
    none of the advice helpers depend on it.
    """
    task_lower = task.lower()
    mode = determine_mode(vectors)
    agent_matches = match_agents(task_lower)
    has_agent_match = bool(agent_matches)

    advice_parts: list[str] = []
    advice_parts.extend(_agent_match_advice(agent_matches))
    advice_parts.extend(_investigation_routing_advice(task_lower, has_agent_match))
    advice_parts.extend(_blindspot_advice(task_lower, mode, vectors))
    advice_parts.extend(_mode_based_advice(mode, vectors, task_lower, has_agent_match))
    advice_parts.extend(_epistemic_workflow_advice(task_lower))

    if not advice_parts:
        return None
    return "\n".join(advice_parts)


def _build_aap_context(prompt: str) -> str:
    """Build the <aap-hedge-detected> block for AAP-enabled hedging prompts.

    Returns empty string if AAP disabled, prompt too short, or no hedges
    detected. Extracted from main() to keep main below complexity threshold.
    """
    aap_config = load_aap_config()
    if not aap_config.get("enabled") or not prompt or len(prompt) <= 15:
        return ""
    hedges = detect_hedges(prompt)
    if not hedges:
        return ""
    hedge_lines = [
        f"  - [{h['category']}] {h['deobfuscation']}"
        for h in hedges[:3]  # Max 3 to avoid overwhelming
    ]
    return (
        "<aap-hedge-detected>\n"
        "User language contains hedging patterns. Per AAP protocol:\n"
        + "\n".join(hedge_lines) + "\n"
        "Surface the actual epistemic content. Don't mirror the hedging.\n"
        "</aap-hedge-detected>"
    )


def main():
    """Main hook handler."""
    try:
        input_data = json.loads(sys.stdin.read()) if not sys.stdin.isatty() else {}
    except (json.JSONDecodeError, EOFError):
        input_data = {}

    prompt = input_data.get("prompt", "")

    # Skip very short prompts or commands
    if len(prompt) < 10 or prompt.startswith("/"):
        print(json.dumps({"continue": True}))
        return

    # Get current epistemic state
    session_id, vectors = get_active_session_vectors()

    # Build routing advice
    advice = build_routing_advice(prompt, vectors, session_id)

    # AAP hedge detection
    aap_context = _build_aap_context(prompt)

    # Investigation depth proportionality (Tx-AB) — fires on hypothesis
    # markers and proportional-scope phrasing ("I think", "maybe", "quick
    # check", etc.). Counters the over-investigation pattern where agents
    # read 30+ files on a question the user already framed with a working
    # theory. Cheap detection (regex + word-boundary) so misses are
    # graceful. Block acknowledges nuance internally so false positives
    # cost ~10 lines of context, no behavior break.
    proportionality_check = build_investigation_proportionality_check(prompt)
    if proportionality_check:
        # Tx-AG: also arm the Sentinel-side budget so the discipline is
        # enforceable, not just suggested. Empirically, the soft block
        # alone got ignored (8 searches in David's 2026-05-06 test).
        # Codex hook payload uses session_id at the top level.
        _arm_proportionality_budget(input_data.get("session_id", ""))

    # EPP semantic pushback check — always-on for substantive prompts.
    # Injected LAST in context_parts to exploit attention recency bias.
    # Phase 0 (2026-04-07) verified effect across Opus/Sonnet/Haiku.
    semantic_check = build_semantic_pushback_check(prompt)

    # Combine contexts. Order matters: routing first (high-level mode),
    # then aap (hedge correction), then proportionality (scope sizing),
    # then EPP (pushback handling) LAST for attention-recency.
    context_parts = []
    if advice:
        context_parts.append(f"<epistemic-routing>\n{advice}\n</epistemic-routing>")
    if aap_context:
        context_parts.append(aap_context)
    if proportionality_check:
        context_parts.append(proportionality_check)
    if semantic_check:
        # Placed LAST — highest attention weight in the injected context window
        context_parts.append(semantic_check)

    output = (
        {"continue": True, "context": "\n".join(context_parts)}
        if context_parts
        else {"continue": True}
    )
    print(json.dumps(output))


if __name__ == "__main__":
    main()

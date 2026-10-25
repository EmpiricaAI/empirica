"""
Epistemic Agent - Sub-agent with Turtle Principle

Spawns agents that:
1. Inherit persona profiles (epistemic priors as PREFLIGHT)
2. Work within investigation branches
3. Report POSTFLIGHT vectors when done
4. Aggregate via auto-merge scoring

Usage:
    from empirica.core.agents import spawn_epistemic_agent, EpistemicAgentConfig

    # Spawn agent with persona
    config = EpistemicAgentConfig(
        session_id="abc123",
        persona_id="security_expert",
        task="Review authentication code for vulnerabilities",
        investigation_path="security-review"
    )

    result = spawn_epistemic_agent(config)
    # result.branch_id, result.postflight_vectors, result.findings
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from empirica.core.persona import PersonaManager, PersonaProfile
from empirica.data.session_database import SessionDatabase

UTC = timezone.utc


@dataclass
class EpistemicAgentConfig:
    """Configuration for spawning an epistemic agent."""

    # Required
    session_id: str
    task: str  # What the agent should do

    # Persona (load by ID or provide directly)
    persona_id: str | None = None
    persona: PersonaProfile | None = None

    # Branch tracking
    investigation_path: str | None = None  # Auto-generated if not provided

    # Transaction linkage (for epistemic continuity)
    transaction_id: str | None = None  # Parent's transaction ID

    # Behavior
    max_tokens: int = 50000
    timeout_minutes: int = 30
    require_postflight: bool = True

    # Context injection
    parent_context: str | None = None  # Additional context from parent
    findings_so_far: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Set default persona and generate investigation path if not provided."""
        if not self.persona_id and not self.persona:
            self.persona_id = "general"  # Default persona

        if not self.investigation_path:
            # Generate from task (first 30 chars, slugified)
            slug = self.task[:30].lower().replace(" ", "-").replace("/", "-")
            self.investigation_path = f"agent-{slug}"


@dataclass
class EpistemicAgentResult:
    """Result from an epistemic agent execution."""

    # Identity
    branch_id: str
    agent_id: str
    persona_id: str

    # Epistemic state
    preflight_vectors: dict[str, float]
    postflight_vectors: dict[str, float] | None
    learning_delta: dict[str, float]

    # Output
    findings: list[str]
    unknowns: list[str]
    output: str

    # Metrics
    tokens_spent: int
    time_spent_minutes: float
    merge_score: float | None

    # Status
    success: bool
    error: str | None = None


def load_persona(config: EpistemicAgentConfig) -> PersonaProfile:
    """Load persona from config or by ID."""
    if config.persona:
        return config.persona

    manager = PersonaManager()
    try:
        return manager.load_persona(config.persona_id)
    except FileNotFoundError:
        # Return default persona with balanced priors.
        # Must satisfy persona schema requirements used by agent-spawn.
        from empirica.core.persona import EpistemicConfig, PersonaProfile, SigningIdentityConfig

        return PersonaProfile(
            persona_id="general",
            name="General Agent",
            version="1.0.0",
            signing_identity=SigningIdentityConfig(
                user_id="system",
                identity_name="general_agent",
                # Must be 64 hex chars; using a stable placeholder that matches schema.
                # (Real identities are used for signing in production personas.)
                public_key="0" * 64,
            ),
            epistemic_config=EpistemicConfig(
                priors={
                    "engagement": 0.70,
                    "know": 0.50,
                    "do": 0.50,
                    "context": 0.50,
                    "clarity": 0.50,
                    "coherence": 0.50,
                    "signal": 0.50,
                    "density": 0.50,
                    "state": 0.50,
                    "change": 0.30,
                    "completion": 0.10,
                    "impact": 0.50,
                    "uncertainty": 0.50,
                },
                # Required by schema; used by Sentinel / persona matching.
                focus_domains=["general"],
            ),
        )


def create_investigation_branch(db: SessionDatabase, config: EpistemicAgentConfig, persona: PersonaProfile) -> str:
    """Create investigation branch for agent tracking."""

    preflight_vectors = persona.epistemic_config.priors

    # create_branch returns the generated branch_id
    branch_id = db.branches.create_branch(
        session_id=config.session_id,
        branch_name=f"agent-{config.persona_id or 'general'}",
        investigation_path=config.investigation_path,
        git_branch_name="",  # Sub-agents don't create git branches
        preflight_vectors=preflight_vectors,
        transaction_id=config.transaction_id,  # Link to parent's transaction
    )

    return branch_id


def format_agent_prompt(config: EpistemicAgentConfig, persona: PersonaProfile, branch_id: str) -> str:
    """
    Format the prompt for an epistemic agent.

    Includes:
    - Persona context (priors, thresholds, focus domains)
    - Task description
    - POSTFLIGHT reporting instructions
    """

    priors = persona.epistemic_config.priors
    thresholds = persona.epistemic_config.thresholds
    domains = persona.epistemic_config.focus_domains

    prompt = f"""# Epistemic Agent Task

## Your Persona: {persona.name}

You are operating as a specialized epistemic agent with the following profile:

**Focus Domains:** {", ".join(domains) if domains else "General"}

**Starting Epistemic State (your priors):**
- Knowledge (know): {priors.get("know", 0.5):.2f}
- Uncertainty: {priors.get("uncertainty", 0.5):.2f}
- Context: {priors.get("context", 0.5):.2f}
- Clarity: {priors.get("clarity", 0.5):.2f}

**Decision Thresholds:**
- Investigate if uncertainty > {thresholds.get("uncertainty_trigger", 0.4)}
- Proceed if confidence > {thresholds.get("confidence_to_proceed", 0.75)}

---

## Task

{config.task}

---

## Context from Parent Agent
{config.parent_context or "No additional context provided."}

## Findings So Far
{chr(10).join(f"- {f}" for f in config.findings_so_far) if config.findings_so_far else "None yet."}

---

## Required: POSTFLIGHT Report

When you complete your work, you MUST include a POSTFLIGHT block at the end of your response.
This reports your updated epistemic state after investigation.

Format (JSON in code block):

```postflight
{{
  "branch_id": "{branch_id}",
  "vectors": {{
    "know": <0.0-1.0: How much do you now know about this?>,
    "uncertainty": <0.0-1.0: How uncertain are you now?>,
    "context": <0.0-1.0: How well do you understand the context?>,
    "clarity": <0.0-1.0: How clear is your understanding?>,
    "completion": <0.0-1.0: How complete is the task?>,
    "impact": <0.0-1.0: What's the potential impact of findings?>
  }},
  "findings": [
    "Key finding 1",
    "Key finding 2"
  ],
  "unknowns": [
    "Things still unclear or needing investigation"
  ],
  "summary": "One-line summary of what you learned"
}}
```

Be HONEST about your vectors. Overconfidence breaks the epistemic loop.
The parent agent will use your POSTFLIGHT to aggregate learnings.

---

Begin your work now.
"""

    return prompt


def parse_postflight(output: str, branch_id: str) -> dict[str, Any] | None:
    """
    Parse POSTFLIGHT block from agent output.

    Looks for ```postflight ... ``` code block.
    """
    import re

    # Try to find postflight block
    pattern = r"```postflight\s*\n(.*?)\n```"
    match = re.search(pattern, output, re.DOTALL)

    if not match:
        # Try alternate formats
        pattern = r'```json\s*\n.*?"branch_id".*?(.*?)\n```'
        match = re.search(pattern, output, re.DOTALL)

    if not match:
        return None

    try:
        data = json.loads(match.group(1) if match.group(1).startswith("{") else "{" + match.group(1) + "}")

        # Validate required fields
        if "vectors" not in data:
            return None

        # Normalize vectors to expected range
        vectors = data["vectors"]
        for key, value in vectors.items():
            if isinstance(value, (int, float)):
                vectors[key] = max(0.0, min(1.0, float(value)))

        return {
            "vectors": vectors,
            "findings": data.get("findings", []),
            "unknowns": data.get("unknowns", []),
            "summary": data.get("summary", ""),
        }
    except (json.JSONDecodeError, KeyError):
        return None


def calculate_learning_delta(preflight: dict[str, float], postflight: dict[str, float]) -> dict[str, float]:
    """Calculate learning delta between preflight and postflight."""
    delta = {}
    key_vectors = ["know", "uncertainty", "context", "clarity", "completion", "impact"]

    for key in key_vectors:
        pre = preflight.get(key, 0.5)
        post = postflight.get(key, 0.5)
        delta[key] = post - pre

    return delta


def spawn_epistemic_agent(
    config: EpistemicAgentConfig,
    execute_fn=None,  # Function to execute agent (for testing/mocking)
) -> EpistemicAgentResult:
    """
    Spawn an epistemic sub-agent.

    This is the main entry point. It:
    1. Loads the persona
    2. Creates an investigation branch
    3. Formats the prompt with epistemic context
    4. Executes the agent (or returns prompt for external execution)
    5. Parses POSTFLIGHT and updates branch

    Args:
        config: Agent configuration
        execute_fn: Optional function to execute agent.
                    If None, returns result with prompt for external execution.

    Returns:
        EpistemicAgentResult with branch_id, vectors, findings, etc.
    """

    db = SessionDatabase()

    try:
        # Load persona
        persona = load_persona(config)

        # Create investigation branch
        branch_id = create_investigation_branch(db, config, persona)

        # Format prompt
        prompt = format_agent_prompt(config, persona, branch_id)

        preflight_vectors = persona.epistemic_config.priors

        # If no execute function, return with prompt for external execution
        if execute_fn is None:
            return EpistemicAgentResult(
                branch_id=branch_id,
                agent_id="pending",
                persona_id=config.persona_id or "general",
                preflight_vectors=preflight_vectors,
                postflight_vectors=None,
                learning_delta={},
                findings=[],
                unknowns=[],
                output=prompt,  # Return prompt as output
                tokens_spent=0,
                time_spent_minutes=0,
                merge_score=None,
                success=False,  # Not yet executed
                error="Agent not executed - prompt returned for external execution",
            )

        # Execute agent
        start_time = datetime.now(UTC)

        try:
            output, tokens = execute_fn(prompt, config.max_tokens)
        except Exception as e:
            return EpistemicAgentResult(
                branch_id=branch_id,
                agent_id="failed",
                persona_id=config.persona_id or "general",
                preflight_vectors=preflight_vectors,
                postflight_vectors=None,
                learning_delta={},
                findings=[],
                unknowns=[],
                output="",
                tokens_spent=0,
                time_spent_minutes=0,
                merge_score=None,
                success=False,
                error=str(e),
            )

        end_time = datetime.now(UTC)
        time_spent = (end_time - start_time).total_seconds() / 60

        # Parse POSTFLIGHT
        postflight_data = parse_postflight(output, branch_id)

        if postflight_data and config.require_postflight:
            postflight_vectors = postflight_data["vectors"]
            findings = postflight_data["findings"]
            unknowns = postflight_data["unknowns"]
        else:
            # Use defaults if no postflight found
            postflight_vectors = preflight_vectors.copy()
            findings = []
            unknowns = []

        # Calculate learning delta
        learning_delta = calculate_learning_delta(preflight_vectors, postflight_vectors)

        # Update branch with postflight
        merge_score = db.branches.checkpoint_branch(
            branch_id=branch_id,
            postflight_vectors=postflight_vectors,
            tokens_spent=tokens,
            time_spent_minutes=int(time_spent),
        )

        return EpistemicAgentResult(
            branch_id=branch_id,
            agent_id=f"agent-{branch_id[:8]}",
            persona_id=config.persona_id or "general",
            preflight_vectors=preflight_vectors,
            postflight_vectors=postflight_vectors,
            learning_delta=learning_delta,
            findings=findings,
            unknowns=unknowns,
            output=output,
            tokens_spent=tokens,
            time_spent_minutes=time_spent,
            merge_score=merge_score,
            success=True,
        )

    finally:
        db.close()


def aggregate_agent_results(
    session_id: str, results: list[EpistemicAgentResult], investigation_round: int = 1
) -> dict[str, Any]:
    """
    Aggregate results from multiple epistemic agents.

    Uses auto-merge scoring to select winner and combine findings.

    Args:
        session_id: Parent session ID
        results: List of agent results
        investigation_round: Round number for merge decision

    Returns:
        Aggregation summary with winner, combined findings, etc.
    """

    if not results:
        return {"error": "No results to aggregate"}

    db = SessionDatabase()

    try:
        # Run auto-merge on all branches
        branch_ids = [r.branch_id for r in results if r.success]

        if not branch_ids:
            return {"error": "No successful agent results"}

        # Get merge decision
        merge_result = db.branches.merge_branches(session_id=session_id, investigation_round=investigation_round)

        # Find winner
        winner = None
        for r in results:
            if r.branch_id == merge_result.get("winning_branch_id"):
                winner = r
                break

        # Combine all findings (winner first, then others)
        all_findings = []
        all_unknowns = []

        if winner:
            all_findings.extend(winner.findings)
            all_unknowns.extend(winner.unknowns)

        for r in results:
            if r.branch_id != (winner.branch_id if winner else None):
                # Add non-winner findings with lower priority
                for f in r.findings:
                    if f not in all_findings:
                        all_findings.append(f)
                for u in r.unknowns:
                    if u not in all_unknowns:
                        all_unknowns.append(u)

        # Calculate aggregate vectors (weighted by merge score)
        total_weight = sum(r.merge_score or 0.1 for r in results if r.success)
        aggregate_vectors = {}

        for key in ["know", "uncertainty", "context", "clarity", "completion", "impact"]:
            weighted_sum = sum(
                (r.postflight_vectors or {}).get(key, 0.5) * (r.merge_score or 0.1) for r in results if r.success
            )
            aggregate_vectors[key] = weighted_sum / total_weight if total_weight > 0 else 0.5

        return {
            "winner": {
                "branch_id": winner.branch_id if winner else None,
                "persona_id": winner.persona_id if winner else None,
                "merge_score": winner.merge_score if winner else None,
                "learning_delta": winner.learning_delta if winner else {},
            },
            "all_agents": [
                {
                    "branch_id": r.branch_id,
                    "persona_id": r.persona_id,
                    "merge_score": r.merge_score,
                    "success": r.success,
                }
                for r in results
            ],
            "combined_findings": all_findings,
            "combined_unknowns": all_unknowns,
            "aggregate_vectors": aggregate_vectors,
            "merge_decision": merge_result,
            "investigation_round": investigation_round,
        }

    finally:
        db.close()


# Convenience function for Claude Code integration
def create_epistemic_agent_prompt(
    session_id: str, task: str, persona_id: str = "general", parent_context: str | None = None
) -> tuple[str, str]:
    """
    Create prompt for spawning via Claude Code Task tool.

    Returns:
        Tuple of (prompt, branch_id) - use branch_id to report results later
    """
    config = EpistemicAgentConfig(
        session_id=session_id, task=task, persona_id=persona_id, parent_context=parent_context
    )

    result = spawn_epistemic_agent(config, execute_fn=None)
    return result.output, result.branch_id

"""
Empirica Epistemic Agents

Sub-agent orchestration with Turtle Principle:
- Agents inherit persona profiles (epistemic priors)
- Agents report vectors back (PREFLIGHT/POSTFLIGHT)
- Agents become investigation branches
- Auto-merge based on epistemic scoring
"""

from .epistemic_agent import (
    EpistemicAgentConfig,
    EpistemicAgentResult,
    format_agent_prompt,
    parse_postflight,
    spawn_epistemic_agent,
)

__all__ = [
    "EpistemicAgentConfig",
    "EpistemicAgentResult",
    "format_agent_prompt",
    "parse_postflight",
    "spawn_epistemic_agent",
]

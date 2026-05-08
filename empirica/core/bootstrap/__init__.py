"""Bootstrap aggregator — three-circle artifact graph injection.

See `docs/specs/PROPOSAL_BOOTSTRAP_AGGREGATOR.md` for the design.

The public surface is `build_bootstrap_payload()` — a pure function consumed
by the CLI hook (post-compact.py / session-init.py), the daemon route
(GET /api/v1/bootstrap), and the MCP tool (mcp__empirica__bootstrap_context).

Three circles, three rules:

  Circle 1  active_state           recency-decayed (per-type half-lives)
  Circle 2  persistent_reference   no decay (fixed budgets)
  Circle 3  topic_relevant_backlog Qdrant similarity to active topic

Wire shape is `schema_version: "2"`.
"""

from .payload import build_bootstrap_payload
from .prompt_relevance import (
    build_prompt_relevance_context,
    get_prompt_relevant_artifacts,
)
from .suggested_links import suggest_links_for_artifact

__all__ = [
    "build_bootstrap_payload",
    "build_prompt_relevance_context",
    "get_prompt_relevant_artifacts",
    "suggest_links_for_artifact",
]

"""get_prompt_relevant_context — surface artifacts relevant to a user prompt.

Called by the tool-router.py UserPromptSubmit hook on every substantive
prompt so the AI's first response is conditioned on prior project
knowledge, not just internal weights.

Reuses the suggested_links Qdrant search machinery (same memory +
assumptions + decisions collections, same legacy reverse-hash fallback)
but takes free text rather than a logged artifact's text and formats
the result as an injectable context block.

Latency-conscious: UserPromptSubmit fires every turn. We cap at 3
candidates by default, time-budget the embedding (Qdrant client has
its own timeouts), and short-circuit on any failure.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .suggested_links import suggest_links_for_artifact

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 3
DEFAULT_SIMILARITY_THRESHOLD = 0.65
MIN_PROMPT_LENGTH = 12  # Below this the embedding is too sparse to be useful


def get_prompt_relevant_artifacts(
    project_id: str,
    prompt: str,
    *,
    project_path: str | Path | None = None,
    top_k: int = DEFAULT_TOP_K,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> list[dict[str, Any]]:
    """Return up to `top_k` artifacts whose embeddings are similar to `prompt`.

    Empty list on any failure: missing project_id, prompt too short,
    Qdrant unreachable, embedding failure, no matches above threshold.
    """
    if not project_id or not prompt:
        return []
    if len(prompt) < MIN_PROMPT_LENGTH:
        return []

    # exclude_id="" — there's no just-logged artifact to exclude
    return suggest_links_for_artifact(
        project_id,
        prompt,
        exclude_id="",
        project_path=project_path,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )


def format_prompt_relevance_context(artifacts: list[dict]) -> str:
    """Render the artifact list as an injectable XML-tagged context block.

    Returns "" if the list is empty so the hook can skip injection.
    The wrapping <prior-context> tag matches the convention used by
    other tool-router blocks (<epistemic-routing>, <hedges>, etc.).
    """
    if not artifacts:
        return ""

    lines = [
        "<prior-context>",
        f"RELEVANT TO YOUR PROMPT — {len(artifacts)} item{'s' if len(artifacts) > 1 else ''} from prior project knowledge:",
    ]
    for art in artifacts:
        type_label = (art.get("type") or "?").replace("_", "-")
        score = art.get("similarity_score")
        score_str = f" {score:.2f}" if isinstance(score, (int, float)) else ""
        summary = (art.get("summary") or "")[:140]
        lines.append(f"- [{type_label}{score_str}] {summary}")
    lines.append(
        'Run `empirica project-search --task "<query>"` for the full list, '
        'or anchor any new artifacts to these via --related-to.'
    )
    lines.append("</prior-context>")
    return "\n".join(lines)


def build_prompt_relevance_context(
    project_id: str | None,
    prompt: str,
    *,
    project_path: str | Path | None = None,
    top_k: int = DEFAULT_TOP_K,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
) -> str:
    """One-call helper for hooks: project_id + prompt → formatted block.

    Combines `get_prompt_relevant_artifacts` and
    `format_prompt_relevance_context`. Returns "" for any failure path.
    """
    if not project_id:
        return ""
    artifacts = get_prompt_relevant_artifacts(
        project_id,
        prompt,
        project_path=project_path,
        top_k=top_k,
        similarity_threshold=similarity_threshold,
    )
    return format_prompt_relevance_context(artifacts)

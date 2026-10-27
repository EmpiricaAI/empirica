"""`cortex investigate` is not a command (ecodex's sweep of 1.14.15, goal 8c106034, finding 4).

Guidance in a skill, two docs, a CLI help string and a handler docstring sent practitioners to `cortex investigate` for mesh-wide retrieval. The
`cortex` binary's verbs are mailbox, practices, outbox, listener, collab, propose and self-update; `investigate` and `search_knowledge` are Cortex
MCP tools. A practitioner who ran the quoted text got `unrecognized subcommand`. Retrieval is named by what it is: the Cortex MCP tool.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SURFACES = (
    "empirica/plugins/claude-code-integration/skills",
    "empirica/cli/parsers",
    "empirica/cli/command_handlers",
    "docs/reference",
    "docs/human/developers",
)
PHRASE = "cortex investigate"


def _files() -> list[Path]:
    return [
        path
        for surface in SURFACES
        for path in sorted((ROOT / surface).rglob("*"))
        if path.suffix in {".md", ".py"} and path.is_file()
    ]


def _offenders(paths: Iterable[Path]) -> list[str]:
    return [str(p) for p in paths if PHRASE in p.read_text(errors="replace")]


def test_the_search_finds_a_planted_mention_and_the_walk_is_not_empty(tmp_path):
    """Positive controls: the detector fires on text that has the phrase, and the real walk covers the surfaces."""
    planted = tmp_path / "guidance.md"
    planted.write_text("For the whole mesh, run `cortex investigate` with your question.")
    clean = tmp_path / "other.md"
    clean.write_text("For the whole mesh, use the Cortex MCP tool `investigate`.")
    assert _offenders([planted, clean]) == [str(planted)]
    assert len(_files()) > 100


def test_no_guidance_tells_the_reader_to_run_a_cortex_investigate_command():
    assert _offenders(_files()) == [], (
        "these still name `cortex investigate`, which the cortex CLI does not have; it is the Cortex MCP tool `investigate`"
    )

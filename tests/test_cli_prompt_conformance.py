"""CLI prompt-parser conformance — the AI-facing anti-drift guard (cortex prop_wsdy3uvl / T2).

Every `empirica <verb>` that the system-prompt templates or a skill tell an AI
to run MUST resolve to a real CLI verb. A *phantom* reference (a verb named in
an AI-facing surface but not implemented) is the exact command-not-found trap
cortex flagged (`lesson-log` / `blindspot-log`): the AI reads the guidance,
runs the verb, gets `command not found`, silently no-ops.

Design (per the T2 decision):
- **Implemented set = LIVE argparse introspection** of `create_argument_parser()`,
  not brittle source regex. The codebase already decided against AST/regex
  source-parsing for CLI (misses modular `parsers/*.py` verbs, can't follow
  `add_*_parsers()`), and `cli_doc_validator.py` regexes `cli_core.py` only.
- **Referenced set = the AI-facing surfaces** (prompt templates + skills), NOT
  `docs/` (that's the human-facing surface `cli_doc_validator` already covers).
- **Only referenced-not-implemented is asserted.** Implemented-not-documented is
  NOT a failure — most of the 85+ verbs are intentionally absent from the lean
  prompt (skills-on-demand); that's by design, not drift.

This runs in CI so the phantom-verb class can't silently re-bake.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLUGIN = _REPO_ROOT / "empirica" / "plugins" / "claude-code-integration"

# AI-facing surfaces an AI reads and may act on.
_AI_SURFACES = [
    _PLUGIN / "templates",  # *.md system-prompt templates (lean + full)
    _PLUGIN / "skills",  # */SKILL.md
]

# A command reference = `empirica <verb>` at the START of a CODE context (an
# inline `backtick span` or a line inside a ``` fenced block ```). Scoping to
# code contexts is deliberate: it excludes English prose where "empirica" is an
# adjective ("reference an empirica goal", "your empirica session") — that's a
# noun phrase, not a command, and matching it produces false positives.
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_CMD_IN_CODE = re.compile(r"^(?:\$\s*)?empirica\s+([a-z][a-z0-9-]+)")

# Tokens that legitimately follow `empirica ` in a code span but are NOT
# empirica-core top-level verbs. Keep this tiny + explicit — every entry is a
# deliberate, documented exception, not a silencer for real drift.
_NON_VERB_ALLOWLIST = {
    "help",  # `empirica help <category>` — argparse built-in help topic
    "fleet",  # services-auditor references `empirica fleet` as an explicitly-
    # labelled SEPARATE PRODUCT (multi-host fleet view), not a core verb.
}


def _implemented_verbs() -> set[str]:
    """Authoritative implemented-verb set via live argparse introspection."""
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    verbs: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            verbs.update(action.choices.keys())  # includes aliases
    return verbs


def _referenced_verbs() -> dict[str, set[str]]:
    """Every `empirica <verb>` referenced *as a command* (in a code context)
    across the AI-facing surfaces → the files that reference it."""
    refs: dict[str, set[str]] = {}
    for root in _AI_SURFACES:
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            text = md.read_text(encoding="utf-8", errors="ignore")
            # Code contexts only: inline `spans` + lines inside ``` fences ```.
            spans: list[str] = list(_INLINE_CODE.findall(text))
            for block in _FENCE.findall(text):
                spans.extend(block.splitlines())
            for span in spans:
                m = _CMD_IN_CODE.match(span.strip())
                if m:
                    refs.setdefault(m.group(1), set()).add(md.name)
    return refs


def test_introspection_yields_the_real_verb_set():
    """Guard the introspection itself: if create_argument_parser() shape drifts
    and yields ~zero verbs, the phantom test below would vacuously pass."""
    verbs = _implemented_verbs()
    assert len(verbs) > 50, f"live introspection returned only {len(verbs)} verbs — parser shape changed?"
    # spot-check a few load-bearing verbs are present
    for core in ("preflight-submit", "check-submit", "finding-log", "goals-create"):
        assert core in verbs, f"expected core verb {core!r} missing from introspected set"


def test_no_phantom_verbs_in_prompt_or_skills():
    """No AI-facing surface may reference an `empirica <verb>` that doesn't exist."""
    implemented = _implemented_verbs()
    referenced = _referenced_verbs()
    phantoms = {
        verb: files for verb, files in referenced.items() if verb not in implemented and verb not in _NON_VERB_ALLOWLIST
    }
    assert not phantoms, (
        "AI-facing prompt/skills reference verbs that don't exist in the CLI "
        "(the command-not-found trap — ship the verb, fix the reference, or "
        "add to _NON_VERB_ALLOWLIST if genuinely not a verb):\n"
        + "\n".join(f"  - `empirica {v}`  in {sorted(files)}" for v, files in sorted(phantoms.items()))
    )


# --- T2.1 (cortex co-design) -------------------------------------------------

# Help-text signal words that mark a flag as load-bearing. If a flag's --help
# says this, the flag matters enough that an AI should be told about it.
_LOAD_BEARING_MARKERS = (
    "load-bearing",
    "load bearing",
    "future-you",
    "essential",
    "required for calibration",
)


_PARSERS_DIR = _REPO_ROOT / "empirica" / "cli" / "parsers"
_FLAG_RE = re.compile(r"--[a-z][a-z0-9-]+")


def _load_bearing_flags() -> dict[str, str]:
    """Flags a parser source-line marks load-bearing → the parser file it's in.

    NOTE — this is SOURCE-based, unlike the runtime introspection used for the
    hard phantom check. The "load-bearing" designation lives in *comments /
    prose next to* ``add_argument`` (e.g. "The --prevention flag is the
    load-bearing field for future-you"), NOT in the flag's runtime ``help=``
    string — so live introspection can't see it. Source-grep is acceptable here
    because this feeds a SOFT advisory report, not a hard assertion; brittleness
    degrades a punch-list, it can't break CI or mis-fail a real command."""
    out: dict[str, str] = {}
    if not _PARSERS_DIR.exists():
        return out
    for py in _PARSERS_DIR.rglob("*.py"):
        for line in py.read_text(encoding="utf-8", errors="ignore").splitlines():
            low = line.lower()
            if any(m in low for m in _LOAD_BEARING_MARKERS):
                for flag in _FLAG_RE.findall(line):
                    out.setdefault(flag, py.name)
    return out


def _ai_surface_text() -> str:
    """All AI-facing prompt + skill text concatenated (for membership checks)."""
    chunks: list[str] = []
    for root in _AI_SURFACES:
        if not root.exists():
            continue
        for md in root.rglob("*.md"):
            chunks.append(md.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(chunks)


def test_load_bearing_flags_documented():
    """SOFT report: flags whose --help declares them load-bearing SHOULD be
    mentioned somewhere in the prompt/skills. Report-only by default (emits a
    warning + the punch-list) so CI shows the count without failing; set
    ``PYTEST_STRICT_DOC_SYNC=1`` to make it a hard fail for pre-release runs.

    The failure mode this guards is epistemic-completeness (an AI never told
    about `--prevention`/`--why-failed` logs the mistake/dead-end without the
    load-bearing field), not execution — hence soft, not the phantom hard-fail."""
    import os
    import warnings

    surface = _ai_surface_text()
    undocumented = {flag: verb for flag, verb in _load_bearing_flags().items() if flag not in surface}
    if not undocumented:
        return
    msg = "Load-bearing flags not mentioned in prompt/skills: " + ", ".join(
        f"{flag} (on {verb})" for flag, verb in sorted(undocumented.items())
    )
    if os.environ.get("PYTEST_STRICT_DOC_SYNC") == "1":
        raise AssertionError(msg)
    warnings.warn(msg, stacklevel=2)


def test_group_verbs_resolve_and_nested_refs_are_not_phantoms():
    """Cortex's edge case: verbs with nested subparsers (e.g. `empirica loop
    register`, `empirica mailbox poll`) must resolve. The extractor tokenizes
    on the FIRST token after `empirica`, so a nested ref maps to the top-level
    group verb — which is in the introspected set, so it's never a phantom."""
    implemented = _implemented_verbs()
    for group in ("loop", "mailbox", "sentinel"):
        assert group in implemented, f"group verb {group!r} missing from introspected set"
    # A nested-subcommand reference extracts the top-level verb, not the sub.
    m = _CMD_IN_CODE.match("empirica loop register")
    assert m is not None and m.group(1) == "loop"
    assert m.group(1) in implemented

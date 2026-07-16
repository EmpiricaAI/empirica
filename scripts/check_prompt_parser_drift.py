#!/usr/bin/env python3
"""Parser↔prompt drift check (T2, co-designed with cortex).

Introspects empirica's CLI (`create_argument_parser`) for the LIVE verb set,
then sweeps the prompt corpus for `empirica <verb>` mentions and diffs both
directions:

  DRIFT (fails, exit 1): a prompt references `empirica <verb>` for a verb the
    parser no longer has — the #348 failure mode (prune a verb, leave a dangling
    reference in a skill / system prompt).
  COVERAGE (report only, exit 0): parser verbs never mentioned in any prompt —
    allowlist-filtered (setup / internal verbs). Informational.

Mentions are read ONLY from Markdown code spans (inline `...` and ```fences```),
never prose — so "your empirica session" (a noun) is not mistaken for a verb.

Corpus:
  in-repo (CI-runnable): the skills + the shipped system-prompt template — the
    surfaces actually LOADED AS PROMPTS. General docs are the /code-docs-align
    lane, not this.
  --include-private (local only): the operator's ~/.claude/empirica-*.md
    includes, where the densest verb guidance lives (CI can't see them).

Usage:
  python scripts/check_prompt_parser_drift.py                    # CI mode
  python scripts/check_prompt_parser_drift.py --include-private  # + ~/.claude
  python scripts/check_prompt_parser_drift.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Prompt surfaces (loaded as prompts) — NOT general docs.
_IN_REPO_GLOBS = [
    "empirica/plugins/claude-code-integration/skills/**/*.md",
    "empirica/plugins/claude-code-integration/templates/*.md",
]
_PRIVATE_GLOB = "empirica-*.md"  # under ~/.claude/

# Markdown code spans (fenced first, then inline) — the only place a real CLI
# invocation lives; prose is excluded to avoid noun false-positives.
_CODE_SPAN = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
# `empirica <verb>` where the verb reads as an actual invocation: it must be
# followed by a flag (` -`), a pipe/continuation, or end-of-line. This rejects
# prose-in-code-spans like `empirica fleet` (product name), "empirica
# transaction is open", "from empirica side" — where a noun follows the token.
_MENTION = re.compile(r"\bempirica\s+([a-z][a-z0-9][a-z0-9-]*)(?=\s+-|\s*[|\\]|\s*$)", re.MULTILINE)

# Verbs intentionally NOT part of the AI-prompt surface — coverage gaps here are
# expected, not drift. (Aliases need no entry: they're in the live verb set, so
# a mention of `empirica fl` resolves fine.)
_ALLOW_UNMENTIONED = frozenset(
    {
        "help",
        "onboard",
        "setup-claude-code",
        "plugin-sync",
        "enp-setup",
        "diagnose",
        "doctor",
        "release",
        "serve",
        "chat",
        "query",
        "edit-with-confidence",
        "mco-load",
        "system-status",
        "forgejo-publish",
        "training-export",
    }
)


def live_verbs() -> set[str]:
    """Top-level verb names + aliases from the live argparse tree."""
    from empirica.cli.cli_core import create_argument_parser

    parser = create_argument_parser()
    verbs: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            verbs |= set(action.choices.keys())
    return verbs


def corpus_files(include_private: bool) -> list[Path]:
    files: list[Path] = []
    for glob in _IN_REPO_GLOBS:
        files += sorted(_REPO.glob(glob))
    if include_private:
        files += sorted((Path.home() / ".claude").glob(_PRIVATE_GLOB))
    return files


def mentions_in(text: str) -> set[str]:
    """`empirica <verb>` tokens found in the code spans of `text`."""
    code = "\n".join(_CODE_SPAN.findall(text))
    return set(_MENTION.findall(code))


def scan(files: list[Path], verbs: set[str]) -> tuple[dict[str, list[str]], set[str]]:
    """Return (drift {verb -> [relpaths]}, mentioned {verb})."""
    drift: dict[str, list[str]] = {}
    mentioned: set[str] = set()
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _relpath(path)
        for verb in mentions_in(text):
            if verb in verbs:
                mentioned.add(verb)
            else:
                drift.setdefault(verb, [])
                if rel not in drift[verb]:
                    drift[verb].append(rel)
    return drift, mentioned


def _relpath(path: Path) -> str:
    try:
        return str(path.relative_to(_REPO))
    except ValueError:
        return str(path)  # private ~/.claude file — outside the repo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Check for drift between CLI verbs and prompt mentions.")
    ap.add_argument("--include-private", action="store_true", help="Also scan ~/.claude/empirica-*.md (local only)")
    ap.add_argument("--json", action="store_true", help="JSON output")
    args = ap.parse_args(argv)

    verbs = live_verbs()
    files = corpus_files(args.include_private)
    drift, mentioned = scan(files, verbs)
    uncovered = sorted(v for v in verbs if v not in mentioned and v not in _ALLOW_UNMENTIONED)

    result = {
        "ok": not drift,
        "verbs": len(verbs),
        "corpus_files": len(files),
        "drift": dict(sorted(drift.items())),
        "uncovered_count": len(uncovered),
        "uncovered": uncovered,
    }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Parser↔prompt drift: {len(verbs)} verbs vs {len(files)} prompt files")
        if drift:
            print(f"\n❌ DRIFT — {len(drift)} verb(s) referenced in prompts but NOT in the parser:")
            for verb, paths in sorted(drift.items()):
                print(f"  `empirica {verb}` — {', '.join(paths)}")
            print("\nA pruned/renamed verb still has a dangling prompt reference. Fix the prompt or restore the verb.")
        else:
            print("✓ No drift — every `empirica <verb>` in the prompt corpus resolves to a live verb.")
        print(f"\n(coverage: {len(uncovered)} live verb(s) never mentioned in prompts — informational, not a failure)")

    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())

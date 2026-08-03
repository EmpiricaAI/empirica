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
#   *.md   → skills + shipped system-prompt template (code-span invocations)
#   *.yaml → project_skills/ CASCADE fixtures — loaded into the AI's context by
#            project-bootstrap, so a dangling `empirica <verb>` here misleads
#            exactly like one in a skill (this is how the #348 agent-spawn ref
#            survived: the corpus never looked at YAML).
_IN_REPO_GLOBS = [
    "empirica/plugins/claude-code-integration/skills/**/*.md",
    "empirica/plugins/claude-code-integration/templates/*.md",
    "project_skills/*.yaml",
]
_PRIVATE_GLOB = "empirica-*.md"  # under ~/.claude/

# Markdown code spans (fenced first, then inline) — for .md prompts, the only
# place a real CLI invocation lives; prose is excluded to avoid noun false-
# positives. (.yaml fixtures are scanned raw — see mentions_in.)
_CODE_SPAN = re.compile(r"```.*?```|`[^`\n]+`", re.DOTALL)
# `empirica <verb>` — capture the verb whenever it reads as a whole token
# (followed by whitespace or end-of-line). The earlier form only fired on a
# trailing flag/pipe/EOL, so it MISSED every positional-arg and two-word
# subcommand invocation — `empirica investigate "q"`, `empirica note "x"`,
# `empirica source-add https://…`, `empirica mesh status`, `empirica mailbox
# poll` — the exact dangling-reference shapes the check exists to catch. We
# broaden to any token boundary and instead suppress the handful of prose nouns
# that can follow `empirica` inside a code span via _PROSE_NOUNS below.
_MENTION = re.compile(r"\bempirica\s+([a-z][a-z0-9][a-z0-9-]*)(?=\s|$)", re.MULTILINE)
# Nouns (not verbs) that legitimately follow the word "empirica" in a code span
# — product/domain nouns, never CLI verbs. Without this guard the broadened
# regex would flag them as drift. Grows on demand: a genuine prose false-
# positive surfaces as a CI failure → add the noun here (self-correcting).
_PROSE_NOUNS = frozenset(
    {
        "session",
        "sessions",
        "transaction",
        "project",
        "projects",
        "instance",
        "core",
        "cortex",
        "workspace",
        "outreach",
        "extension",
        "autonomy",
        "side",
        "home",
        "server",
        "system",
    }
)

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


def mentions_in(text: str, *, code_spans_only: bool = True) -> set[str]:
    """`empirica <verb>` tokens in `text`, minus known prose nouns.

    code_spans_only=True (Markdown): scan only inside `code spans`, since prose
    freely says "your empirica session". code_spans_only=False (YAML fixtures):
    scan the raw text — YAML string values ARE the invocation lines, there is no
    code-span wrapper to key off.
    """
    scanned = "\n".join(_CODE_SPAN.findall(text)) if code_spans_only else text
    return set(_MENTION.findall(scanned)) - _PROSE_NOUNS


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
        code_spans_only = path.suffix.lower() not in (".yaml", ".yml")
        for verb in mentions_in(text, code_spans_only=code_spans_only):
            if verb in verbs:
                mentioned.add(verb)
            else:
                drift.setdefault(verb, [])
                if rel not in drift[verb]:
                    drift[verb].append(rel)
    return drift, mentioned



# --- Flag validation -------------------------------------------------------
#
# The verb check passed green while THREE unrunnable commands shipped:
#
#     empirica deadend-log --list      # deadend-log is real; --list is not
#     empirica finding-log --list      # same
#     empirica project-search --task   # real flags, but --project-id was required
#
# A phantom FLAG is worse than a phantom verb. A phantom verb fails with
# "unknown command" — unambiguous. A phantom flag on a real verb produces a
# usage error that reads as though the CALLER got it wrong, so an AI following
# the doc blames its own invocation and retries variations of a command that
# can never work.
#
# argparse holds the truth. The guard simply never asked it.

# `empirica <verb> <rest-of-invocation>` — rest stops at a pipe, redirect,
# comment, or end of line, since those begin a different command.
_INVOCATION = re.compile(r"\bempirica\s+([a-z][a-z0-9-]{2,})((?:\s+[^\n|>#]*)?)")

# A long flag. Short flags are not checked: `-` alone is the stdin convention
# used all over the corpus (`empirica preflight-submit -`), and single-dash
# clusters are too ambiguous to judge without shell semantics.
_FLAG = re.compile(r"(?<![\w-])--([a-z][a-z0-9-]*)")

# Quoted argument VALUES are data, not flags. The corpus is full of lines like
#   empirica unknown-log --unknown "CLAUDE.md references --type on project-search"
# where `--type` is the CONTENT being logged. Scanning it as a flag reported the
# one remaining "drift" in the whole corpus, and it was prose about a flag rather
# than a use of one. Strip quoted spans before extracting.
_QUOTED = re.compile(r"\"[^\"]*\"|'[^']*'")

# Flags argparse provides implicitly or that every parser inherits.
_UNIVERSAL_FLAGS = {"help", "version"}


def _long_opts(parser) -> set[str]:
    return {o[2:] for a in parser._actions for o in getattr(a, "option_strings", ()) if o.startswith("--")}


def live_flags_by_verb() -> dict[str, set[str]]:
    """Long option names each command accepts, keyed by "verb" and "verb sub".

    GROUP commands must be resolved two levels deep. `empirica loop register
    --name X` puts `--name` on the `register` subparser, not on `loop` — a
    one-level lookup reports every group-command flag in the corpus as drift.
    Checking the real corpus surfaced 11 such false positives before this;
    shipping them would have trained everyone to ignore the guard, which is
    worse than not having it.
    """
    from empirica.cli.cli_core import create_argument_parser

    out: dict[str, set[str]] = {}
    parser = create_argument_parser()
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        for verb, sub in action.choices.items():
            out[verb] = set(_UNIVERSAL_FLAGS) | _long_opts(sub)
            for nested in sub._actions:
                if isinstance(nested, argparse._SubParsersAction):
                    for subverb, subparser in nested.choices.items():
                        out[f"{verb} {subverb}"] = set(_UNIVERSAL_FLAGS) | _long_opts(sub) | _long_opts(subparser)
    return out


def flag_drift_in(text: str, flags_by_verb: dict[str, set[str]], *, code_spans_only: bool = True) -> set[tuple]:
    """{(verb, flag)} used in `text` that the verb does not accept."""
    scanned = "\n".join(_CODE_SPAN.findall(text)) if code_spans_only else text
    bad: set[tuple] = set()
    for verb, rest in _INVOCATION.findall(scanned):
        # Prefer the two-level key when the next token names a subcommand.
        tokens = rest.split()
        sub = tokens[0] if tokens and not tokens[0].startswith("-") else None
        known = flags_by_verb.get(f"{verb} {sub}") if sub else None
        if known is None:
            known = flags_by_verb.get(verb)
        if known is None:  # unknown verb — the verb check already reports it
            continue
        for flag in _FLAG.findall(_QUOTED.sub(" ", rest)):
            if flag not in known:
                bad.add((verb, flag))
    return bad


def scan_flags(files: list[Path], flags_by_verb: dict[str, set[str]]) -> dict[str, list[str]]:
    """Return {"verb --flag" -> [relpaths]}."""
    drift: dict[str, list[str]] = {}
    for path in files:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = _relpath(path)
        code_spans_only = path.suffix.lower() not in (".yaml", ".yml")
        for verb, flag in flag_drift_in(text, flags_by_verb, code_spans_only=code_spans_only):
            key = f"{verb} --{flag}"
            drift.setdefault(key, [])
            if rel not in drift[key]:
                drift[key].append(rel)
    return drift


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
    flag_drift = scan_flags(files, live_flags_by_verb())
    uncovered = sorted(v for v in verbs if v not in mentioned and v not in _ALLOW_UNMENTIONED)

    result = {
        "ok": not drift and not flag_drift,
        "verbs": len(verbs),
        "corpus_files": len(files),
        "drift": dict(sorted(drift.items())),
        "flag_drift": dict(sorted(flag_drift.items())),
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

        if flag_drift:
            print(f"\n❌ FLAG DRIFT — {len(flag_drift)} flag(s) that the verb does not accept:")
            for key, paths in sorted(flag_drift.items()):
                print(f"  `empirica {key}` — {', '.join(paths)}")
            print(
                "\nA phantom FLAG fails with a usage error that reads as though the CALLER "
                "got it wrong, so a reader blames their own invocation and retries a command "
                "that can never work. Fix the prompt, or add the flag."
            )
        else:
            print("✓ No flag drift — every flag shown on a live verb is one that verb accepts.")

        print(f"\n(coverage: {len(uncovered)} live verb(s) never mentioned in prompts — informational, not a failure)")

    return 1 if (drift or flag_drift) else 0


if __name__ == "__main__":
    sys.exit(main())

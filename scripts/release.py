#!/usr/bin/env python3
"""
Automated Release Script for Empirica
Single source of truth: pyproject.toml version

Usage:
    python scripts/release.py --dry-run                           # Preview full release
    python scripts/release.py                                     # Execute full release
    python scripts/release.py --version-only --old-version 1.5.6  # Update versions only
    python scripts/release.py --old-version 1.5.6                 # Full release with sweep
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _load_shared_cve_waivers() -> list[dict]:
    """The single governed CVE-waiver source, shared with ``empirica
    security-audit`` (``empirica.core.security.waivers.CVE_WAIVERS``) so the two
    gates can't drift. Falls back to ``[]`` if empirica isn't importable at
    release time — which only makes this gate STRICTER, never looser."""
    try:
        from empirica.core.security.waivers import CVE_WAIVERS

        return CVE_WAIVERS
    except Exception:
        return []


# ANSI colors
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"

# Chocolatey package ownership — kars85 is the chocolatey.org account that
# owns the empirica listing. See .nuspec <owners> field; release flow asserts
# they match before the choco push step so a manual nuspec edit doesn't
# silently change ownership.
CHOCOLATEY_OWNER = "kars85"


def log(msg: str, color: str = RESET):
    print(f"{color}{msg}{RESET}")


def _strip_generated_stamp(text: str) -> str:
    """Drop the generator's `**Generated:** <UTC>` line before comparing.

    It changes on every render and carries no content, so including it makes a
    currency check answer "what time is it" instead of "does this match the CLI".
    """
    return "\n".join(ln for ln in text.splitlines() if not ln.startswith("**Generated:**"))


def error_soft(msg: str):
    """Report a failed check without exiting — the caller decides."""
    log(f"❌ {msg}", RED)


def error(msg: str):
    log(f"❌ ERROR: {msg}", RED)
    sys.exit(1)


def warning(msg: str):
    log(f"⚠️  WARNING: {msg}", YELLOW)


def success(msg: str):
    log(f"✅ {msg}", GREEN)


def info(msg: str):
    log(f"ℹ️  {msg}", BLUE)


# ── README's What's New and What's Coming ────────────────────────────────────
# The section people read first. What's New is the release's author-written `### Highlights` (short lines); the detail stays in
# CHANGELOG.md and what is left out is counted and linked, never dropped silently. What's Coming renders docs/ROADMAP.yaml, a forecast
# whose items each carry a confidence, the kind of evidence it rests on, and the observation that would falsify it.

_WHATS_NEW_FALLBACK_MAX = 6
_WHATS_NEW_EARLIER = 2
_SUMMARY_MAX = 160
_ROADMAP_TEXT_FIELDS = ("id", "title", "prediction", "basis", "falsifier")
_ROADMAP_FIELDS = (*_ROADMAP_TEXT_FIELDS, "confidence", "evidence", "status")
_ROADMAP_STATUSES = ("predicted", "shipped", "dropped", "retracted", "superseded")
_RELEASE_NUMBER = re.compile(r"^\d+\.\d+\.\d+$")
_REVISION_KEYS = ("on", "from", "to", "reason")
# Fields whose silent change would let a forecast be adjusted after the fact. Changing one needs a dated revision saying why.
_ROADMAP_GUARDED = ("confidence", "prediction", "falsifier", "horizon", "evidence")
# How the basis is known caps the confidence it can carry: a figure a command produced can support 0.95, a report from someone else
# (testimony about code that may have changed since) no more than 0.7, and an inference no more than 0.6.
_ROADMAP_EVIDENCE_CEILING = {"measured": 0.95, "reviewed": 0.85, "reported": 0.7, "inferred": 0.6}
# The roadmap is public text. Ids of proposals, records, goals, tasks and commits are internal coordinates a reader cannot resolve. A
# hex run counts as a commit hash only if it mixes letters and digits, so dates, counts and ordinary words pass.
_INTERNAL_ID = re.compile(
    r"\b(?:prop|ser)[_-][0-9a-z]{6,}|\b(?:goal|task)[ _-][0-9a-f]{6,}\b|\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b",
    re.IGNORECASE,
)
_NOT_A_SENTENCE_END = r"(?<!e\.g)(?<!i\.e)(?<!\bvs)(?<!\betc)(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bSt)"
_SENTENCE = re.compile(r"(.+?" + _NOT_A_SENTENCE_END + r"[.!?])(?:\s|$)")
_NEW_HEADING = re.compile(r"^## What[’']s New in ")
_COMING_HEADING = re.compile(r"^## What[’']s Coming\s*$")
_ANY_TOP_HEADING = re.compile(r"^#{1,2} ")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _changelog_entries(text: str) -> list[tuple[str, str]]:
    """(version, body) per `## [version]` heading, newest first. The body is everything after the heading line."""
    parts = re.split(r"^## \[([^\]]+)\][^\n]*\n", text, flags=re.MULTILINE)
    return [(parts[i], parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def _entry_sections(body: str) -> tuple[str, dict[str, str]]:
    """(intro paragraph, {section name: text}) for one CHANGELOG entry."""
    parts = re.split(r"^### (.+)$", body, flags=re.MULTILINE)
    return parts[0].strip(), {parts[i].strip(): parts[i + 1] for i in range(1, len(parts) - 1, 2)}


def _top_level_bullets(block: str) -> list[str]:
    """Each `- ` bullet with its indented continuation lines joined into one line."""
    items: list[str] = []
    current: str | None = None
    for raw in block.split("\n"):
        if raw.startswith("- "):
            if current is not None:
                items.append(current)
            current = raw[2:].strip()
        elif current is not None and raw.startswith("  ") and raw.strip():
            current += " " + raw.strip()
        elif current is not None:
            items.append(current)
            current = None
    if current is not None:
        items.append(current)
    return items


def _bold_title(bullet: str) -> str | None:
    m = re.match(r"\*\*(.+?)\*\*", bullet)
    return m.group(1).strip().rstrip(".") if m else None


def _first_sentence(text: str, cap: int = _SUMMARY_MAX) -> str:
    flat = " ".join(text.split())
    m = _SENTENCE.match(flat)
    out = m.group(1) if m else flat
    return out if len(out) <= cap else out[: cap - 1].rstrip() + "…"


def _highlights(sections: dict[str, str], version: str, strict: bool) -> list[str]:
    """The `### Highlights` bullets of one entry. A block that exists but yields no bullet is an error for the release being cut: falling
    back quietly would publish the detail the author meant to replace and say nothing."""
    name = next((n for n in sections if n.lower().startswith("highlights")), None)
    if name is None:
        return []
    bullets = _top_level_bullets(sections[name])
    if not bullets and strict:
        raise ValueError(
            f"CHANGELOG {version}: `### {name}` has no `- ` bullets, so README's What's New would silently fall back to titles"
        )
    return bullets


def render_whats_new(changelog_text: str, version: str, earlier: int = _WHATS_NEW_EARLIER) -> str:
    """README's `## What's New in <version>` block: the highlights, a counted link to the rest, and the releases before this one."""
    entries = _changelog_entries(changelog_text)
    if not entries or entries[0][0] != version:
        top = entries[0][0] if entries else "none"
        raise ValueError(
            f"CHANGELOG's top entry is {top}, not {version}; README's What's New would describe the wrong release"
        )
    _, sections = _entry_sections(entries[0][1])
    highlights = _highlights(sections, version, strict=True)
    detail = [
        b
        for name, block in sections.items()
        if not name.lower().startswith("highlights")
        for b in _top_level_bullets(block)
    ]
    if highlights:
        shown = highlights
    else:
        shown = [_bold_title(b) or _first_sentence(b) for b in detail][:_WHATS_NEW_FALLBACK_MAX]
    if not shown:
        raise ValueError(
            f"No bullet items found in the CHANGELOG entry for {version}; README's What's New would be synced empty"
        )

    lines = [f"## What's New in {version}", "", *[f"- {item}" for item in shown], ""]
    if highlights:
        lines.append(
            f"All {len(detail)} entries for this release, with the measurements behind each highlight, are in the [CHANGELOG](CHANGELOG.md)."
        )
    else:
        lines.append(f"Plus {len(detail) - len(shown)} more entries in the [CHANGELOG](CHANGELOG.md).")
    previous = []
    for ver, body in entries[1 : 1 + earlier]:
        intro, secs = _entry_sections(body)
        first_highlight = next(iter(_highlights(secs, ver, strict=False)), "")
        first_title = next(
            (t for t in (_bold_title(b) for s, blk in secs.items() for b in _top_level_bullets(blk)) if t), ""
        )
        # The line is already bold at the version; a highlight that opens with its own bold title would nest one in the other.
        summary = re.sub(r"^\*\*(.+?)\*\*", r"\1", _first_sentence(first_highlight or intro or first_title), count=1)
        if summary:
            previous.append(f"- **{ver}** — {summary}")
    if previous:
        lines += ["", "**Earlier releases**", "", *previous]
    return "\n".join(lines)


def _strings_in(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings_in(v)


def validate_roadmap(data: object) -> dict:
    """The forecast behind What's Coming, or ValueError naming the first thing wrong with it."""
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("roadmap needs an `items` list")
    forecast = data.get("forecast")
    if not isinstance(forecast, dict) or not forecast.get("made_in"):
        raise ValueError("roadmap needs `forecast.made_in`, the release the forecast was made in")
    if not isinstance(forecast["made_in"], str):
        raise ValueError(
            f"forecast.made_in must be quoted text, not {forecast['made_in']!r} (an unquoted 1.10 loads as the number 1.1)"
        )
    seen: set[str] = set()
    for n, item in enumerate(data["items"], 1):
        if not isinstance(item, dict):
            raise ValueError(f"roadmap item {n} is not a mapping")
        for field in _ROADMAP_FIELDS:
            if item.get(field) in (None, ""):
                raise ValueError(f"roadmap item {n}: missing {field}")
        for field in _ROADMAP_TEXT_FIELDS:
            if not isinstance(item[field], str):
                raise ValueError(f"roadmap item {n}: {field} must be text, not {type(item[field]).__name__}")
        conf = item["confidence"]
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            raise ValueError(f"roadmap item {n}: confidence must be a number from 0 to 1, not {conf!r}")
        ceiling = _ROADMAP_EVIDENCE_CEILING.get(item["evidence"])
        if ceiling is None:
            raise ValueError(
                f"roadmap item {n}: evidence must be one of {', '.join(_ROADMAP_EVIDENCE_CEILING)}, not {item['evidence']!r}"
            )
        if conf > ceiling:
            raise ValueError(
                f"roadmap item {n}: evidence {item['evidence']!r} cannot carry confidence above {ceiling}, not {conf}"
            )
        if item["status"] not in _ROADMAP_STATUSES:
            raise ValueError(
                f"roadmap item {n}: status must be one of {', '.join(_ROADMAP_STATUSES)}, not {item['status']!r}"
            )
        if item["status"] != "predicted" and not item.get("resolved_in"):
            raise ValueError(
                f"roadmap item {n}: a {item['status']} item needs resolved_in, the release that settled it"
            )
        if not isinstance(item.get("steered"), bool):
            raise ValueError(
                f"roadmap item {n}: steered must be true or false (was a goal for it already open when it was predicted?)"
            )
        horizon = item.get("horizon")
        if horizon is None and item["status"] == "predicted":
            raise ValueError(
                f"roadmap item {n}: a predicted item needs a horizon, the release by which it will be settled"
            )
        if horizon is not None and not (isinstance(horizon, str) and _RELEASE_NUMBER.match(horizon)):
            raise ValueError(f"roadmap item {n}: horizon must be a release number like 1.15.0, not {horizon!r}")
        if item["status"] in ("retracted", "superseded") and not (
            isinstance(item.get("reason"), str) and item["reason"].strip()
        ):
            raise ValueError(
                f"roadmap item {n}: a {item['status']} item needs a reason (why the prediction was wrong or replaced)"
            )
        revisions = item.get("revisions")
        if revisions is not None:
            complete = (
                isinstance(revisions, list)
                and revisions
                and all(
                    isinstance(r, dict) and all(r.get(k) not in (None, "") for k in _REVISION_KEYS) for r in revisions
                )
            )
            if not complete:
                hint = " (an unquoted `on:` key is the boolean true in YAML 1.1: quote the `on` key)" if any(
                    isinstance(r, dict) and True in r for r in revisions
                ) else ""
                raise ValueError(f"roadmap item {n}: revisions need on, from, to and reason in every entry{hint}")
            if revisions[-1]["to"] != conf:
                raise ValueError(
                    f"roadmap item {n}: the last of revisions ends at {revisions[-1]['to']!r} but confidence is {conf!r}"
                )
        if item["id"] in seen:
            raise ValueError(f"roadmap item {n}: duplicate id {item['id']!r}")
        seen.add(item["id"])
    for n, item in enumerate(data["items"], 1):
        if item["status"] == "superseded" and item.get("superseded_by") not in seen:
            raise ValueError(
                f"roadmap item {n}: superseded_by must name another item in the roadmap, not {item.get('superseded_by')!r}"
            )
    for text in (*_strings_in(forecast), *_strings_in(data["items"])):
        if leak := _INTERNAL_ID.search(text):
            raise ValueError(f"roadmap carries an internal id ({leak.group(0)!r}); the roadmap is public text")
    return data


def load_roadmap(path: Path) -> dict:
    import yaml

    return validate_roadmap(yaml.safe_load(path.read_text()))


def _cell(text: object) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def render_whats_coming(roadmap: dict) -> str:
    """README's `## What's Coming` block, or "" when the forecast has nothing open and nothing graded."""
    validate_roadmap(roadmap)
    items = roadmap["items"]
    open_items = [i for i in items if i["status"] == "predicted"]
    count = {s: sum(1 for i in items if i["status"] == s) for s in _ROADMAP_STATUSES}
    unplanned = sum(1 for i in items if i["status"] == "shipped" and not i["steered"])
    if not open_items and not any(v for s, v in count.items() if s != "predicted"):
        return ""
    lines = ["## What's Coming", ""]
    if open_items:
        lines += [
            f"Empirica's own forecast, made in {roadmap['forecast']['made_in']} from its open work, the demand it has measured and its known "
            "weak spots. Each prediction carries a confidence, the evidence behind it and the observation that would prove it wrong, and is "
            "graded when it ships or is dropped. The word beside the confidence says how the basis is known: **measured** by a command, "
            "**reviewed** in the code, **reported** by someone else, or **inferred**; weaker evidence cannot carry a higher confidence. "
            "**planned** means a goal for it already existed when we predicted it: those are scored separately, because delivering planned "
            "work is not foresight.",
            "",
            "| Direction | Confidence | Why we expect it | What would change our mind |",
            "|---|---|---|---|",
        ]
        for i in sorted(open_items, key=lambda it: -it["confidence"]):
            lines.append(
                f"| **{_cell(i['title'])}**: {_cell(i['prediction'])} | {round(i['confidence'] * 100)}% ({i['evidence']}{', planned' if i['steered'] else ''}) | {_cell(i['basis'])} | {_cell(i['falsifier'])} |"
            )
        lines.append("")
    if any(v for s, v in count.items() if s != "predicted"):
        lines.append(
            f"Forecast record: {count['shipped']} shipped ({unplanned} of them not on the plan when predicted), {count['dropped']} dropped, "
            f"{count['retracted']} retracted, {count['superseded']} superseded so far."
        )
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _release_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def items_past_horizon(roadmap: dict, version: str) -> list[dict]:
    """Predicted items whose horizon release has already been cut: nothing stays open indefinitely."""
    return [
        i
        for i in roadmap["items"]
        if i["status"] == "predicted" and _release_tuple(i["horizon"]) < _release_tuple(version)
    ]


def roadmap_silent_edits(released: dict, current: dict) -> list[str]:
    """What changed against the last RELEASED roadmap without leaving a trace, one message per problem.

    A forecast that can be adjusted after the fact is not a forecast. A changed confidence, prediction, falsifier, horizon or evidence on
    a predicted item needs a new dated revision saying why; a resolved item cannot be rewritten; and an item cannot be deleted, only
    retracted, dropped or superseded, so the record keeps its misses. Drafts before the first release are free (no baseline).
    """
    problems = []
    now = {i["id"]: i for i in current.get("items", [])}
    for old in released.get("items", []):
        item = now.get(old["id"])
        if item is None:
            problems.append(
                f"{old['id']}: removed from the roadmap; retract, drop or supersede it instead of deleting it"
            )
            continue
        if old["status"] != "predicted":
            # A resolved item is closed history: nothing about it may change, including the fields outside _ROADMAP_GUARDED
            # (status, resolved_in, reason, steered, title, basis), or a miss could be reopened and a flag flipped unseen.
            rewritten = sorted(k for k in {*old, *item} if old.get(k) != item.get(k))
            if rewritten:
                problems.append(
                    f"{old['id']}: {', '.join(rewritten)} changed on an item that was already {old['status']}"
                )
            continue
        if old.get("steered") != item.get("steered"):
            # steered says a goal already existed when the prediction was made; it is a fact about then, not an opinion to revise.
            problems.append(
                f"{old['id']}: steered changed from {old.get('steered')!r} to {item.get('steered')!r}; it records the moment of prediction"
            )
        changed = [f for f in _ROADMAP_GUARDED if old.get(f) != item.get(f)]
        if not changed:
            continue
        if old["status"] == "predicted" and item["status"] == "predicted":
            if len(item.get("revisions") or []) <= len(old.get("revisions") or []):
                problems.append(
                    f"{old['id']}: {', '.join(changed)} changed with no new revision; append one under `revisions` saying why"
                )
        else:
            problems.append(
                f"{old['id']}: {', '.join(changed)} rewritten after the item was resolved or while resolving it"
            )
    return problems


def replace_whats_new_block(readme: str, block: str) -> str:
    """README with its What's New section (and the What's Coming that follows it) replaced by `block`.

    The span is read line by line: it starts at the `## What's New in` heading and ends at the next level-1 or level-2 heading that is
    not What's Coming, or at a `---` divider that does not lead into What's Coming, or at the end of the file. Headings inside a code
    fence do not count. Everything outside the span is returned byte for byte. A What's Coming anywhere else is refused, because
    replacing around it would leave two forecasts.
    """
    lines = readme.split("\n")
    in_fence = False
    start = end = None
    coming_elsewhere: list[int] = []
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if start is None:
            if _NEW_HEADING.match(line):
                start = i
            elif _COMING_HEADING.match(line):
                coming_elsewhere.append(i)
            continue
        if end is not None:
            if _COMING_HEADING.match(line):
                coming_elsewhere.append(i)
            continue
        if line.strip() == "---":
            following = next((ln for ln in lines[i + 1 :] if ln.strip()), "")
            if not _COMING_HEADING.match(following):
                end = i
        elif _ANY_TOP_HEADING.match(line) and not _COMING_HEADING.match(line):
            end = i
    if start is None:
        raise ValueError("no `## What's New in …` heading found")
    if coming_elsewhere:
        raise ValueError(
            f"README has a What's Coming heading outside the What's New block (line {coming_elsewhere[0] + 1}); "
            "move it directly under What's New so the sync can replace both"
        )
    after = lines[end:] if end is not None else []
    return "\n".join([*lines[:start], *block.split("\n"), "", *after])


# Run by the Python-floor gate under the oldest supported interpreter: parse every file the suite or the package imports. A SyntaxError
# there is a syntax the floor cannot read, whatever the newer interpreters on the box accept.
PYTHON_FLOOR_PARSE_SCRIPT = """
import ast, json, pathlib
errors, checked = [], 0
for root in ("scripts", "empirica", "tests"):
    for path in sorted(pathlib.Path(root).rglob("*.py")):
        checked += 1
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as e:
            errors.append([path.as_posix(), e.lineno, e.msg])
        except (UnicodeDecodeError, ValueError) as e:
            # Not UTF-8, or a null byte: a file the interpreter cannot read is a finding about the tree, not a reason to crash.
            errors.append([path.as_posix(), 0, f"unreadable: {type(e).__name__}: {e}"])
print(json.dumps({"checked": checked, "errors": errors}))
"""


class ReleaseManager:
    def __init__(
        self,
        dry_run: bool = False,
        old_version: str | None = None,
        skip_tests: bool = False,
        commit_bump: bool = False,
        local_artifacts: bool = False,
    ):
        self.dry_run = dry_run
        # Escape hatch: publish artifacts from this machine IN ADDITION to CI.
        # Default False — the tag triggers release.yml, which owns every channel.
        self.local_artifacts = local_artifacts
        self.repo_root = Path(__file__).parent.parent
        self.version: str | None = None
        self.old_version: str | None = old_version
        self.tarball_sha256: str | None = None
        # When True, --version-only commits its own sweep (allowlist only, never
        # `git add -A`) so no manual commit step can sweep a concurrent session's
        # uncommitted work into the release commit (the 1.12.28 ERM-sweep).
        self.commit_bump = commit_bump
        # When True, --prepare skips the full pytest re-run (the ~12min gate)
        # and relies on develop CI's green run for the full suite. The fast
        # gates (import/ruff/pyright/pip-audit) still run.
        self.skip_tests = skip_tests
        # develop HEAD sha captured before the merge→main, so the trust-CI
        # check can match the release commit against develop's CI run.
        self.develop_head: str | None = None

    def read_version(self) -> str:
        """Read version from pyproject.toml"""
        pyproject_path = self.repo_root / "pyproject.toml"
        if not pyproject_path.exists():
            error(f"pyproject.toml not found at {pyproject_path}")

        content = pyproject_path.read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if not match:
            error("Could not find version in pyproject.toml")

        version = match.group(1)
        info(f"Version from pyproject.toml: {version}")
        return version

    def calculate_sha256(self) -> str:
        """Calculate SHA256 of the tarball"""
        tarball_pattern = f"empirica-{self.version}.tar.gz"
        dist_dir = self.repo_root / "dist"
        tarball = dist_dir / tarball_pattern

        if not tarball.exists():
            if self.dry_run:
                info(f"Tarball not found (dry run): {tarball}")
                return "0" * 64
            error(f"Tarball not found: {tarball}")

        sha256 = hashlib.sha256()
        with open(tarball, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                sha256.update(chunk)

        sha256_hex = sha256.hexdigest()
        info(f"Tarball SHA256: {sha256_hex}")
        return sha256_hex

    def update_homebrew_formula(self):
        """Update Homebrew formula with new version and SHA256"""
        formula_path = self.repo_root / "packaging/homebrew/empirica.rb"
        if not formula_path.exists():
            warning(f"Homebrew formula not found: {formula_path}")
            return

        content = formula_path.read_text()

        # Update URL — handle both PyPI and GitHub release URL formats
        url_pattern = r'url "https://[^"]+/empirica-[^"]+\.tar\.gz"'
        new_url = f'url "https://files.pythonhosted.org/packages/source/e/empirica/empirica-{self.version}.tar.gz"'
        content = re.sub(url_pattern, new_url, content)

        # Update assert_match version
        assert_pattern = r'assert_match "[^"]+", shell_output'
        new_assert = f'assert_match "{self.version}", shell_output'
        content = re.sub(assert_pattern, new_assert, content)

        # Update SHA256
        sha_pattern = r'sha256 "[a-f0-9]{64}"'
        new_sha = f'sha256 "{self.tarball_sha256}"'
        content = re.sub(sha_pattern, new_sha, content)

        if not self.dry_run:
            formula_path.write_text(content)
            success(f"Updated Homebrew formula: {formula_path}")
        else:
            info(f"Would update Homebrew formula: {formula_path}")

    # The ONLY path Homebrew reads in a third-party tap that has a Formula/ dir.
    #
    # Homebrew/brew Library/Homebrew/tap.rb:
    #     potential_formula_dirs = [path/"Formula", path/"HomebrewFormula", path]
    #     formula_dir = potential_formula_dirs.find(&:directory?) || (path/"Formula")
    #
    # `find` returns the FIRST existing directory, so a tap with a Formula/ dir
    # never reads root-level .rb files. EmpiricaAI/homebrew-tap has had Formula/
    # since 2026-05-11 (ecodex v0.0.1) while every empirica release from 1.12.x
    # through 1.13.7 wrote empirica.rb to the tap ROOT — pushed cleanly, verified
    # present, and invisible to `brew install`. The push succeeded; the publish
    # did not. Write where brew looks, not where the file happens to live.
    TAP_FORMULA_RELPATH = "Formula/empirica.rb"

    def update_homebrew_tap(self):
        """Copy updated formula to the Homebrew tap repo and push"""
        log("\n" + "=" * 60)
        log("🍺 Updating Homebrew tap")
        log("=" * 60)

        local_formula = self.repo_root / "packaging/homebrew/empirica.rb"
        if not local_formula.exists():
            warning(f"Local formula not found: {local_formula}")
            warning("Skipping homebrew tap update — compliance release_chain check will surface this on next run.")
            return

        # Look for tap repo in common locations
        tap_candidates = [
            self.repo_root.parent / "homebrew-tap",  # sibling dir
            Path.home() / "empirical-ai" / "homebrew-tap",  # home dir
        ]

        # Identify a tap by its .git, not by a formula file: keying the search on
        # `empirica.rb` made the candidate probe depend on the very layout this
        # function is responsible for producing, so a correctly-laid-out tap
        # (formula under Formula/) would have read as "not a tap at all".
        info(f"Searching for tap repo (looking for {len(tap_candidates)} candidate paths)")
        tap_repo = None
        for candidate in tap_candidates:
            if (candidate / ".git").exists():
                tap_repo = candidate
                info(f"  ✓ found at: {tap_repo}")
                break
            else:
                info(f"  ✗ not at:   {candidate} (no .git)")

        if tap_repo is None:
            warning("Homebrew tap repo not found. Manual step needed:")
            warning(f"  cp {local_formula} <your-tap-repo>/{self.TAP_FORMULA_RELPATH}")
            warning(f"  cd <your-tap-repo> && git commit -am 'Update empirica to {self.version}' && git push")
            warning("Compliance release_chain check will surface this gap until republished.")
            return

        tap_formula = tap_repo / self.TAP_FORMULA_RELPATH

        if not self.dry_run:
            tap_formula.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_formula, tap_formula)
            success(f"Copied formula to {tap_formula}")

            # Commit and push. Same outcome-assertion as the version bump: the
            # commit runs with check=False, so an unchanged formula is a silent
            # no-op and `git push` then pushes nothing — while this reported the
            # tap "updated and pushed". This is the channel that has historically
            # needed hand-reconciliation, so a false success here is the expensive
            # kind.
            head_before = self._git_head(tap_repo)
            self.run_command(["git", "add", self.TAP_FORMULA_RELPATH], cwd=str(tap_repo))
            self.run_command(
                ["git", "commit", "-m", f"Update empirica to {self.version}"], cwd=str(tap_repo), check=False
            )
            head_after = self._git_head(tap_repo)
            if head_after == head_before:
                warning(
                    f"Homebrew tap NOT updated — no commit was created in {tap_repo} (HEAD still "
                    f"{head_before[:8]}). The formula there already matches this build, or the copy "
                    "did not change it. Nothing was pushed."
                )
                return
            self.run_command(["git", "push"], cwd=str(tap_repo))
            success(f"Homebrew tap updated and pushed: {tap_repo} ({head_after[:8]})")
        else:
            info(f"Would copy {local_formula} → {tap_formula}")
            info(f"Would commit and push in {tap_repo}")

    def update_dockerfile(self):
        """Update Dockerfile with new version"""
        dockerfile_path = self.repo_root / "Dockerfile"
        if not dockerfile_path.exists():
            warning(f"Dockerfile not found: {dockerfile_path}")
            return

        content = dockerfile_path.read_text()

        # Update version label
        content = re.sub(r'LABEL version="[^"]+"', f'LABEL version="{self.version}"', content)

        # Update wheel filename in COPY
        content = re.sub(
            r"COPY dist/empirica-[^-]+-py3-none-any\.whl",
            f"COPY dist/empirica-{self.version}-py3-none-any.whl",
            content,
        )

        # Update wheel filename in RUN pip install
        content = re.sub(
            r"/tmp/empirica-[^-]+-py3-none-any\.whl",
            f"/tmp/empirica-{self.version}-py3-none-any.whl",
            content,
            count=2,  # Both COPY and RUN lines
        )

        if not self.dry_run:
            dockerfile_path.write_text(content)
            success(f"Updated Dockerfile: {dockerfile_path}")
        else:
            info(f"Would update Dockerfile: {dockerfile_path}")

    def update_chocolatey_nuspec(self):
        """Update Chocolatey nuspec with new version"""
        nuspec_path = self.repo_root / "packaging/chocolatey/empirica.nuspec"
        if not nuspec_path.exists():
            warning(f"Chocolatey nuspec not found: {nuspec_path}")
            return

        content = nuspec_path.read_text()

        # Update version
        content = re.sub(r"<version>[^<]+</version>", f"<version>{self.version}</version>", content)

        if not self.dry_run:
            nuspec_path.write_text(content)
            success(f"Updated Chocolatey nuspec: {nuspec_path}")
        else:
            info(f"Would update Chocolatey nuspec: {nuspec_path}")

    def update_chocolatey_checksum(self):
        """Update SHA256 checksum in Chocolatey install script"""
        install_ps1 = self.repo_root / "packaging/chocolatey/tools/chocolateyinstall.ps1"
        if not install_ps1.exists():
            warning(f"Chocolatey install script not found: {install_ps1}")
            return

        if not self.tarball_sha256:
            warning("No SHA256 available — skipping Chocolatey checksum update")
            return

        content = install_ps1.read_text()
        content = re.sub(
            r"\$checksum\s*=\s*'[a-fA-F0-9]+'",
            f"$checksum = '{self.tarball_sha256}'",
            content,
        )
        if not self.dry_run:
            install_ps1.write_text(content)
            success(f"Updated Chocolatey checksum: {install_ps1}")
        else:
            info(f"Would update Chocolatey checksum: {install_ps1}")

    def build_and_push_chocolatey(self):
        """Build Chocolatey .nupkg and push to chocolatey.org.

        Push uses the Chocolatey REST API directly (PUT to
        push.chocolatey.org/api/v2/package/) rather than `choco push`
        subprocess. The CLI returns 400 on `push.chocolatey.org/`
        (issue #97); kars85 verified the REST endpoint works during
        the 1.8.14 manual push. Pack stays via `choco pack` since it
        produces the Windows-native .nupkg via the choco binary.
        """
        log("\n" + "=" * 60)
        log("🍫 Building and pushing Chocolatey package")
        log("=" * 60)

        if not shutil.which("choco"):
            info("choco CLI not found — skipping Chocolatey publish (run from Windows or a Choco-enabled CI runner)")
            return

        choco_dir = self.repo_root / "packaging/chocolatey"
        nuspec = choco_dir / "empirica.nuspec"
        if not nuspec.exists():
            warning(f"Chocolatey nuspec not found: {nuspec}")
            return

        # Guard against silent ownership drift: the chocolatey.org listing is
        # owned by CHOCOLATEY_OWNER, and pushing from any other account fails
        # with a 403. Verify the nuspec hasn't been edited away from that.
        nuspec_text = nuspec.read_text(encoding="utf-8")
        if f"<owners>{CHOCOLATEY_OWNER}</owners>" not in nuspec_text:
            error(
                f"Chocolatey nuspec <owners> does not match expected "
                f"'{CHOCOLATEY_OWNER}'. Update {nuspec} or change "
                f"CHOCOLATEY_OWNER in scripts/release.py."
            )

        nupkg = choco_dir / f"empirica.{self.version}.nupkg"

        self.run_command(["choco", "pack"], cwd=str(choco_dir))
        success(f"Built: {nupkg}")

        if not nupkg.exists() and not self.dry_run:
            error(f"Expected .nupkg not found: {nupkg}")

        api_key = os.environ.get("CHOCOLATEY_API_KEY")
        if not api_key:
            # HARD FAIL, not warn-and-return. This gate was wrong about
            # availability, not merely quiet: its own message named
            # `choco apikey set` as an alternative, so a key stored in
            # Chocolatey's own credential store satisfies `choco push` while this
            # check reports it missing and skips. Built package, exit 0, nothing
            # published.
            #
            # Found by ecodex on 2026-08-05 in the identical shape: their
            # `release.sh --publish-crates` gated on CARGO_REGISTRY_TOKEN while
            # cargo reads ~/.cargo/credentials.toml natively — the token WAS
            # there and the script never tried, caught only by checking crates.io
            # rather than the exit code.
            #
            # The sharper version of "a channel that cannot run must say so": a
            # gate must be right about whether the channel CAN run, and an env
            # var is not where most tools keep their credentials.
            error(
                "CHOCOLATEY_API_KEY not set — refusing to skip silently.\n"
                "   The .nupkg is built. If your key is in Chocolatey's own store "
                "(`choco apikey set`), this REST push cannot read it:\n"
                "   export CHOCOLATEY_API_KEY=<key>   # or push manually with `choco push`"
            )

        if self.dry_run:
            info(f"Would PUT {nupkg} to https://push.chocolatey.org/api/v2/package/ (REST)")
            return

        # REST API push — fixes #97 (`choco push` CLI returns 400)
        try:
            import requests
        except ImportError:
            error("`requests` not available; cannot push to Chocolatey via REST API")
            return

        push_url = "https://push.chocolatey.org/api/v2/package/"
        headers = {
            "X-NuGet-ApiKey": api_key,
            "Content-Type": "application/octet-stream",
        }
        try:
            with open(nupkg, "rb") as f:
                response = requests.put(
                    push_url,
                    headers=headers,
                    data=f,
                    timeout=300,
                )
        except requests.RequestException as e:
            error(f"Chocolatey REST push failed: {e}")
            return

        if response.status_code in (200, 201, 202):
            success(f"Pushed to chocolatey.org: empirica {self.version} (REST {response.status_code})")
        else:
            error(f"Chocolatey REST push returned {response.status_code}: {response.text[:500]}")

    def update_version_strings(self):
        """Update version strings in all source files not covered by other methods.

        Covers: __init__.py (both packages), empirica-mcp/pyproject.toml,
        setup_claude_code.py, install.sh, plugin.json, README and install docs,
        Dockerfile.alpine. Repo files only: a release
        never writes the box it runs on.
        """
        version_files = [
            # (path, pattern, replacement)
            (
                self.repo_root / "empirica" / "__init__.py",
                r'__version__\s*=\s*"[^"]+"',
                f'__version__ = "{self.version}"',
            ),
            # empirica_mcp's own __version__. MISSING from this sweep until 1.13.48:
            # it sat at "1.8.14" for roughly five minors while pyproject beside it
            # read the current release — wrong in the repo AND in every install, so
            # no reinstall could fix it. One package, three version surfaces, all
            # disagreeing, on the evening a fleet was trying to work out which
            # code it was running.
            (
                self.repo_root / "empirica-mcp" / "empirica_mcp" / "__init__.py",
                r'__version__\s*=\s*"[^"]+"',
                f'__version__ = "{self.version}"',
            ),
            (
                self.repo_root / "empirica-mcp" / "pyproject.toml",
                r'^version\s*=\s*"[^"]+"',
                f'version = "{self.version}"',
            ),
            # empirica-mcp's core dep is a floor at the current version with a
            # <2 ceiling; bump the floor in lockstep so upgrading the wrapper
            # still pulls core up to match. (It was ==, which rolled core back.)
            (
                self.repo_root / "empirica-mcp" / "pyproject.toml",
                r'"empirica>=[0-9]+\.[0-9]+\.[0-9]+,<2"',
                f'"empirica>={self.version},<2"',
            ),
            (
                self.repo_root / "empirica" / "cli" / "command_handlers" / "setup_claude_code.py",
                r'PLUGIN_VERSION\s*=\s*"[^"]+"',
                f'PLUGIN_VERSION = "{self.version}"',
            ),
            (
                self.repo_root / "empirica" / "plugins" / "claude-code-integration" / "install.sh",
                r'PLUGIN_VERSION="[^"]+"',
                f'PLUGIN_VERSION="{self.version}"',
            ),
            (
                self.repo_root / "empirica" / "plugins" / "claude-code-integration" / ".claude-plugin" / "plugin.json",
                r'"version":\s*"[^"]+"',
                f'"version": "{self.version}"',
            ),
            # __init__.py docstring version
            (
                self.repo_root / "empirica" / "__init__.py",
                r"^Version:\s*[0-9]+\.[0-9]+\.[0-9]+",
                f"Version: {self.version}",
            ),
            # README.md version badge and footer
            (
                self.repo_root / "README.md",
                r"badge/version-[0-9]+\.[0-9]+\.[0-9]+-blue\)\]\(https://github\.com/EmpiricaAI/empirica/releases/tag/v[0-9]+\.[0-9]+\.[0-9]+\)",
                f"badge/version-{self.version}-blue)](https://github.com/EmpiricaAI/empirica/releases/tag/v{self.version})",
            ),
            # README.md docker tag references (standalone — added 1.8.16 to plug the
            # gap that left "nubaeon/empirica:1.8.14" lying around after the 1.8.15
            # release_check sweep)
            (
                self.repo_root / "README.md",
                r"nubaeon/empirica:[0-9]+\.[0-9]+\.[0-9]+(-alpine)?",
                lambda m: f"nubaeon/empirica:{self.version}{m.group(1) or ''}",
            ),
            # README.md author footer: `**Version:** 1.8.X` (bold-markdown form
            # — earlier `^Version:` regex only matched the bare __init__.py
            # docstring form). Added after the 1.8.14→1.8.16 sweep gap left
            # the footer stuck on the older version.
            (
                self.repo_root / "README.md",
                r"\*\*Version:\*\*\s+[0-9]+\.[0-9]+\.[0-9]+",
                f"**Version:** {self.version}",
            ),
            # docs/human/end-users/02_INSTALLATION.md — pip pin + docker tags
            (
                self.repo_root / "docs" / "human" / "end-users" / "02_INSTALLATION.md",
                r"pip install empirica==[0-9]+\.[0-9]+\.[0-9]+",
                f"pip install empirica=={self.version}",
            ),
            (
                self.repo_root / "docs" / "human" / "end-users" / "02_INSTALLATION.md",
                r"nubaeon/empirica:[0-9]+\.[0-9]+\.[0-9]+(-alpine)?",
                lambda m: f"nubaeon/empirica:{self.version}{m.group(1) or ''}",
            ),
            # MCP server reference. (The system prompt needs no entry: the lean
            # template carries {{ empirica_version }} and is rendered at install.)
            (
                self.repo_root / "docs" / "human" / "developers" / "MCP_SERVER_REFERENCE.md",
                r"\*\*Version:\*\*\s+[0-9]+\.[0-9]+\.[0-9]+",
                f"**Version:** {self.version}",
            ),
            # Chocolatey install script version
            (
                self.repo_root / "packaging" / "chocolatey" / "tools" / "chocolateyinstall.ps1",
                r"\$packageVersion\s*=\s*'[^']+'",
                f"$packageVersion = '{self.version}'",
            ),
            # PROJECT_CONFIG version
            (
                self.repo_root / ".empirica-project" / "PROJECT_CONFIG.yaml",
                r'version:\s*"[^"]+"',
                f'version: "{self.version}"',
            ),
            # docs/README.md current-version pointer (the one legit hit
            # that broken-sweep_version used to catch — the other 31
            # were historical refs that should NOT be rewritten)
            (
                self.repo_root / "docs" / "README.md",
                r"\*\*Version:\*\*\s+[0-9]+\.[0-9]+\.[0-9]+",
                f"**Version:** {self.version}",
            ),
            # docs/human/developers/EXTENDING_EMPIRICA.md "**Version:**" header
            (
                self.repo_root / "docs" / "human" / "developers" / "EXTENDING_EMPIRICA.md",
                r"\*\*Version:\*\*\s+[0-9]+\.[0-9]+\.[0-9]+",
                f"**Version:** {self.version}",
            ),
        ]

        # Dockerfile.alpine (same patterns as Dockerfile)
        alpine_path = self.repo_root / "Dockerfile.alpine"
        if alpine_path.exists():
            content = alpine_path.read_text()
            content = re.sub(r'LABEL version="[^"]+"', f'LABEL version="{self.version}"', content)
            content = re.sub(
                r"COPY dist/empirica-[^-]+-py3-none-any\.whl",
                f"COPY dist/empirica-{self.version}-py3-none-any.whl",
                content,
            )
            content = re.sub(
                r"/tmp/empirica-[^-]+-py3-none-any\.whl",
                f"/tmp/empirica-{self.version}-py3-none-any.whl",
                content,
                count=2,
            )
            if not self.dry_run:
                alpine_path.write_text(content)
                success(f"Updated: {alpine_path}")
            else:
                info(f"Would update: {alpine_path}")

        for filepath, pattern, replacement in version_files:
            if not filepath.exists():
                warning(f"Not found: {filepath}")
                continue

            content = filepath.read_text()
            if not re.search(pattern, content, flags=re.MULTILINE):
                # "Already up to date" used to cover this too: a pattern that
                # no longer matches the file (the file's form changed) skipped
                # the bump silently, and the release shipped the old value.
                warning(f"Version pattern matched NOTHING in {filepath}: {pattern} — not bumped")
                continue
            new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)

            if content == new_content:
                info(f"Already up to date: {filepath}")
                continue

            if not self.dry_run:
                filepath.write_text(new_content)
                success(f"Updated: {filepath}")
            else:
                info(f"Would update: {filepath}")

    def clear_bytecode_cache(self):
        """Clear __pycache__ so stale .pyc files don't shadow new version strings.

        Called after `update_version_strings` so long-running editable-install
        Python processes (e.g., empirica CLI between releases) don't report
        stale `__version__`.
        """
        cleared = 0
        for pycache in self.repo_root.rglob("__pycache__"):
            if pycache.is_dir():
                shutil.rmtree(pycache, ignore_errors=True)
                cleared += 1
        if cleared:
            info(f"Cleared {cleared} __pycache__ directories")

    # NOTE: `sweep_version` was removed in 1.9.9.
    #
    # It did a naive `content.replace(old_version, self.version)` across every
    # .md/.py/.toml/.yaml file in the repo, which rewrote historical version
    # references ("shipped in v1.9.6", "(v1.9.6+)" feature markers, test section
    # headers, etc.) into false history. The 1.9.7 → 1.9.8 cycle produced 32
    # working-tree changes — only 1 was a legit current-version pointer.
    #
    # Replacement: every legit current-version pointer file has an explicit
    # regex pattern in `update_version_strings`. Missing patterns are added
    # there as we discover them — that's a noticed-and-corrected miss, not a
    # silent false-history rewrite.

    def regenerate_cli_docs(self):
        """Regenerate CLI_COMMANDS_UNIFIED.md so the 'Framework version' header
        reflects the freshly-bumped __version__.

        The generator reads `empirica.__version__` (already bumped by
        update_version_strings); without this step the doc lags releases by
        one version and gets surfaced via cockpit / statusline / `empirica
        --help`. Non-fatal: a generator error is logged as a warning,
        release continues.
        """
        log("\n" + "=" * 60)
        log("📚 Regenerating CLI_COMMANDS_UNIFIED.md")
        log("=" * 60)

        generator = self.repo_root / "scripts" / "generate_cli_docs.py"
        if not generator.exists():
            warning("scripts/generate_cli_docs.py not found, skipping CLI docs regen")
            return

        if self.dry_run:
            info(f"[DRY RUN] Would run: python {generator}")
            return

        try:
            result = subprocess.run(
                ["python", str(generator)],
                cwd=str(self.repo_root),
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode != 0:
                warning(f"CLI docs regen exited {result.returncode}: {result.stderr.strip()[:200]}")
            else:
                success("CLI_COMMANDS_UNIFIED.md regenerated")
        except Exception as exc:
            warning(f"CLI docs regen failed: {exc}")

    def verify_docs_ready(self):
        """Gate: the release-facing docs are already authored, on this branch.

        The split this enforces (David, 2026-08-05): **authoring is not a release
        step.** Writing README's What's New and regenerating the CLI reference are
        reasoning-adjacent work that belongs on develop, where the author is and
        where review happens. ``--prepare`` used to DO them, after the checkout to
        main, and that one choice produced three separate defects:

        - **Write-on-main with no path back.** main accumulated a README and a CLI
          reference develop had never seen, so every release merge conflicted on
          exactly those two files.
        - **A window where the version led the docs.** The bump had to be
          committed before ``--prepare`` could check out main, so ``pyproject``
          said 1.13.5 while README still said 1.13.4 — precisely the state that
          shipped 1.13.4 advertising "What's New in 1.13.3".
        - **A silent skip.** The sync warned and returned on four paths and the
          release continued regardless.

        Now: run ``--docs`` on develop, review the diff, commit it with the bump.
        ``--prepare`` only checks, and refuses to proceed if the check fails. A
        gate cannot ship the wrong thing quietly; an action can.
        """
        log("\n" + "=" * 60)
        log("📚 Verifying release docs are authored (not authoring them)")
        log("=" * 60)

        self.verify_changelog_entry()

        readme_path = self.repo_root / "README.md"
        if not readme_path.exists():
            error("README.md not found")
        heading = re.search(r"^## What's New in (\S+)", readme_path.read_text(), re.MULTILINE)
        if not heading:
            error("README.md has no `## What's New in …` section to verify")
        if heading.group(1) != self.version:
            error(
                f"README's What's New reads {heading.group(1)}, but this release is {self.version}.\n"
                f"   Run `python scripts/release.py --docs` on develop, review the diff, and commit it\n"
                f"   with the version bump — the README must not lag the version it ships."
            )
        success(f"README What's New is authored for {self.version}")

        if self._cli_docs_stale():
            error(
                "docs/human/developers/CLI_COMMANDS_UNIFIED.md is out of date with the CLI.\n"
                "   Run `python scripts/release.py --docs` on develop and commit the regenerated file."
            )
        success("CLI reference is current")

        # The version sweep is deterministic, but it is still a WRITE, so it moved
        # to --docs with the rest. Verify it landed rather than re-running it here:
        # __init__.py is the cheapest witness that the sweep ran at this version.
        init_py = self.repo_root / "empirica" / "__init__.py"
        if init_py.exists():
            swept = re.search(r'__version__\s*=\s*"([^"]+)"', init_py.read_text())
            if swept and swept.group(1) != self.version:
                error(
                    f"empirica/__init__.py says {swept.group(1)} but this release is {self.version} — "
                    f"the version sweep has not been committed.\n"
                    f"   Run `python scripts/release.py --docs` on develop and commit it."
                )
        success(f"Version sweep is committed at {self.version}")

    def _cli_docs_stale(self) -> bool:
        """True when regenerating the CLI reference would change it.

        Compares against a temp render rather than trusting an mtime: the file is
        generated, so 'someone ran the generator' and 'the output matches the CLI'
        are different questions and only the second one matters.

        The generator stamps a `**Generated:** <UTC>` line, which differs on every
        render and is not content. Comparing raw text made the check fire on the
        clock — a gate that always trips is indistinguishable from one that never
        does, because both stop being read.
        """
        target = self.repo_root / "docs" / "human" / "developers" / "CLI_COMMANDS_UNIFIED.md"
        generator = self.repo_root / "scripts" / "generate_cli_docs.py"
        if not generator.exists() or not target.exists():
            warning("CLI docs generator or target missing — skipping currency check")
            return False
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            probe = Path(td) / "cli.md"
            res = subprocess.run(
                [sys.executable, str(generator), "--output", str(probe)],
                capture_output=True,
                text=True,
                cwd=str(self.repo_root),
            )
            if res.returncode != 0:
                error(f"CLI docs generator failed ({res.returncode}): {res.stderr.strip()[:300]}")
            return _strip_generated_stamp(probe.read_text()) != _strip_generated_stamp(target.read_text())

    def run_verify(self):
        """``--verify``: did the release actually LAND on every channel?

        `--publish` is tag-and-push, so CI owns delivery — and a CI job's
        `success` is not evidence that it published. On v1.13.7 the Docker and
        Homebrew jobs both concluded `success` with every substantive step
        SKIPPED, because a secret-check step gated them and skipping is not
        failing. PyPI and GitHub landed; Docker and the tap silently did not, and
        nothing in the pipeline noticed.

        So this checks ARTIFACTS, never job status, and each check uses the
        instrument that answers the question actually being asked:

        - PyPI: the **simple index**, which is what pip resolves against. Both
          JSON fields (`.info.version` and `.releases`) lag behind it by minutes.
        - Docker: the per-tag endpoint, not the paginated tag list, which is
          ordered by last-update and caches.
        - Homebrew: the formula at the path brew RESOLVES (Formula/empirica.rb)
          on the tap's remote head — not a local clone, and not the tap root.
        """
        import urllib.request

        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Release — VERIFY (artifacts, not job status)     ║")
        log("╚════════════════════════════════════════════════════════════╝\n")
        self.version = self.read_version()
        v = self.version

        def _get(url: str) -> str | None:
            try:
                with urllib.request.urlopen(url, timeout=15) as r:
                    return r.read().decode("utf-8", "replace")
            except Exception:
                return None

        results: list[tuple[str, bool, str]] = []

        # A FETCH FAILURE IS NOT AN ABSENCE. `_get` returns None on any network
        # error, and reporting that as "absent from the simple index" tells the
        # operator the release did not land when the truth is that the check
        # could not look — then names `--publish --local-artifacts` as the
        # remedy, which races the GitHub release to fix a channel that is fine.
        #
        # Observed on the 1.13.39 cut: three consecutive verifies disagreed with
        # each other and with a direct curl. Retried, because a transient is the
        # common case and a real absence stays absent.
        for pkg, fname in (("empirica", f"empirica-{v}"), ("empirica-mcp", f"empirica_mcp-{v}")):
            body = None
            for _ in range(3):
                body = _get(f"https://pypi.org/simple/{pkg}/")
                if body:
                    break
                time.sleep(2)
            if body is None:
                results.append(
                    (
                        f"PyPI {pkg}",
                        False,
                        "COULD NOT REACH the simple index after 3 tries — this is UNKNOWN, not "
                        "absent. Do not republish on this; curl the index yourself first.",
                    )
                )
                continue
            ok = fname in body
            results.append((f"PyPI {pkg}", ok, "simple index" if ok else "absent from the simple index"))

        for tag in (v, f"{v}-alpine"):
            ok = _get(f"https://hub.docker.com/v2/repositories/nubaeon/empirica/tags/{tag}") is not None
            results.append((f"Docker {tag}", ok, "present" if ok else "tag not found"))

        gh = subprocess.run(
            ["gh", "release", "view", f"v{v}", "--json", "assets", "-q", ".assets|length"],
            capture_output=True,
            text=True,
            timeout=20,
            cwd=str(self.repo_root),
        )
        n = gh.stdout.strip() if gh.returncode == 0 else "0"
        results.append(
            (f"GitHub v{v}", gh.returncode == 0 and n not in ("", "0"), f"{n} asset(s)" if n else "no release")
        )

        # Read the formula brew would actually resolve, at the path brew reads
        # (see TAP_FORMULA_RELPATH), and assert it carries THIS version. The old
        # check was `git ls-remote HEAD` — a reachability ping whose success
        # message literally said "check the formula version", i.e. it deferred
        # the only question that mattered. It passed on 1.13.7 while the tap
        # served no empirica formula at all.
        tap_raw = _get(f"https://raw.githubusercontent.com/EmpiricaAI/homebrew-tap/HEAD/{self.TAP_FORMULA_RELPATH}")
        tap_ok = bool(tap_raw and v in tap_raw)
        if not tap_raw:
            tap_detail = f"no formula at {self.TAP_FORMULA_RELPATH} — brew cannot resolve it"
        elif not tap_ok:
            tap_detail = f"{self.TAP_FORMULA_RELPATH} exists but does not carry {v}"
        else:
            tap_detail = f"{self.TAP_FORMULA_RELPATH} carries {v}"
        results.append(("Homebrew tap", tap_ok, tap_detail))

        # Dependency-CLOSURE check: the unit a user installs is `pip install -U
        # empirica empirica-mcp`, not either package alone. Until 1.13.48
        # empirica-mcp pinned `empirica==<v>`, so if the sibling lagged on PyPI,
        # `-U` on a box with both resolves the OLD mcp, whose == pin DOWNGRADES
        # empirica — a self-reverting release that every per-package check reports
        # green (mesh report prop_tmmiftrs). Verify what the user RECEIVES, not
        # what you uploaded. Best-effort: a venv/network failure is a WARN (the
        # per-channel checks stand); a wrong RESOLVED version is a hard miss.
        closure_ok, closure_detail = self._verify_install_closure(v)
        if closure_ok is None:
            info(f"Install closure: {closure_detail}")
        else:
            results.append(("Install closure (pip -U both)", closure_ok, closure_detail))

        for name, ok, detail in results:
            (success if ok else error_soft)(f"{name}: {detail}")

        missing = [n for n, ok, _ in results if not ok]
        if missing:
            error(
                f"{len(missing)} channel(s) did not land: {', '.join(missing)}\n"
                f"   CI job status is NOT evidence of publication — a gated skip still concludes success.\n"
                f"   Recover with: python scripts/release.py --publish --local-artifacts"
            )
        success(f"All {len(results)} channels carry {v}")

    def _verify_install_closure(self, v: str):
        """Resolve `pip install -U empirica empirica-mcp` in a throwaway venv and
        assert BOTH land on ``v``. Returns (ok, detail); ok is None (skip → WARN)
        on a venv/pip/network failure so an environment hiccup doesn't fail the
        release, but a wrong RESOLVED version is a hard (ok=False) miss.

        This is the check that would have caught the self-reverting 1.13.10: with
        empirica-mcp lagging on PyPI, unpinned `-U` pulls the old mcp whose
        `empirica==` pin downgrades empirica — invisible to every per-package
        check (mesh report prop_tmmiftrs).
        """
        import tempfile
        import venv as _venv

        try:
            with tempfile.TemporaryDirectory() as td:
                vdir = Path(td) / "v"
                _venv.create(vdir, with_pip=True)
                pip = vdir / "bin" / "pip"
                inst = subprocess.run(
                    # --no-cache-dir: the throwaway venv still shares ~/.cache/pip,
                    # so right after publish a stale cached index for the PRIOR
                    # version resolves the old pair and false-fails the closure —
                    # exactly when this check runs (diagnosed on 1.13.15; a manual
                    # `pip cache purge` then re-verify went green). Force a fresh
                    # index fetch so the check reads what a first-time user gets.
                    [str(pip), "install", "--no-cache-dir", "-q", "-U", "empirica", "empirica-mcp"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if inst.returncode != 0:
                    return None, f"skipped — clean-venv install failed: {inst.stderr.strip()[:120]}"
                got = {}
                for pkg in ("empirica", "empirica-mcp"):
                    show = subprocess.run([str(pip), "show", pkg], capture_output=True, text=True, timeout=30)
                    for line in show.stdout.splitlines():
                        if line.startswith("Version:"):
                            got[pkg] = line.split(":", 1)[1].strip()
                            break
                if got.get("empirica") == v and got.get("empirica-mcp") == v:
                    return True, f"pip -U resolves empirica {v} + empirica-mcp {v}"
                # An OLD version here is not yet a verdict. `--no-cache-dir`
                # defeats the LOCAL pip cache (added after 1.13.15) but not
                # PyPI's CDN, whose edges propagate independently — measured on
                # 1.13.39, three consecutive verifies resolved 1.13.38, then
                # 1.13.39, then 1.13.38 again, while a direct `pip download
                # --no-cache-dir` fetched the new wheel every time.
                #
                # So: say WHICH it is. A stale resolve that persists is a real
                # self-reverting release; one that flips is propagation. Naming
                # the flip is the point — two disagreeing answers from the same
                # check IS the finding, and reporting either one alone is wrong.
                return False, (
                    f"pip -U resolves empirica {got.get('empirica', '?')} + "
                    f"empirica-mcp {got.get('empirica-mcp', '?')} — either a sibling lag/pin "
                    f"DOWNGRADING the closure, or CDN propagation. Re-run --verify: if it "
                    f"flips to {v} it was propagation; if it stays, the release is "
                    f"self-reverting and needs the sibling republished."
                )
        except Exception as e:
            return None, f"skipped — closure check errored: {str(e)[:120]}"

    def run_docs(self):
        """``--docs``: author the release-facing docs on THIS branch. No merge, no
        build, no publish, and deliberately no commit — the diff is for review.

        Deterministic work (the version sweep, the build, the upload) stays in
        ``--prepare``/``--publish``. This is the half a human should look at.
        """
        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Release — DOCS (author, review, then commit)     ║")
        log("╚════════════════════════════════════════════════════════════╝\n")

        self.version = self.read_version()
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
        ).stdout.strip()
        if branch == "main":
            error(
                "--docs must not run on main. Authoring on main is what left main holding a "
                "README and CLI reference develop had never seen, conflicting every release.\n"
                "   Switch to develop and re-run."
            )
        info(f"Authoring docs for {self.version} on '{branch}'")

        self.verify_changelog_entry()
        self.update_version_strings()
        self.sync_readme_whats_new()
        self.regenerate_cli_docs()

        log("\n" + "=" * 60)
        success(f"Docs authored for {self.version} — NOT committed")
        info("Review the diff, then commit the sweep + CHANGELOG + README together:")
        info("  git diff")
        info(f"  git add -u && git commit -m 'chore(release): bump version to {self.version}'")
        info("Then: python scripts/release.py --prepare")

    def verify_changelog_entry(self):
        """Hard-gate the release on a CHANGELOG entry for THIS version.

        Both surfaces the release derives from CHANGELOG were previously
        ungated, and both failed silently:

        1. **No entry at all.** Nothing checked that ``## [<version>]`` exists,
           so a release could ship with no notes. Measured at 1.13.4: **22
           tagged releases have no CHANGELOG heading** (1.11.9, 1.12.19,
           1.8.17, 1.9.7/8, 1.7.7/12, most of the 1.0-1.6 era).
        2. **Entry exists but isn't on top.** ``sync_readme_whats_new`` reads
           the FIRST ``## `` section, whatever it is — so a leftover
           ``## [Unreleased]``, or the previous release still sitting at the
           top, silently syncs the wrong release's notes into README.

        Checking that the top heading IS this version closes both at once, and
        catches the third observed failure too: a feature commit that writes
        its bullets over the previous release's heading (819e917f0 did exactly
        this to 1.12.19, absorbing a shipped release's notes into the next one)
        leaves the top heading stale, which fails here.
        """
        log("\n" + "=" * 60)
        log("📋 Verifying CHANGELOG entry")
        log("=" * 60)

        changelog_path = self.repo_root / "CHANGELOG.md"
        if not changelog_path.exists():
            error(f"CHANGELOG.md not found at {changelog_path} — cannot release without release notes")

        headings = re.findall(r"^## \[([^\]]+)\]", changelog_path.read_text(), re.MULTILINE)
        if not headings:
            error("CHANGELOG.md has no `## [version]` entries — cannot verify the release entry")

        if headings[0] != self.version:
            error(
                f"CHANGELOG.md's top entry is [{headings[0]}], but this release is {self.version}.\n"
                f"   Write a `## [{self.version}] - YYYY-MM-DD` section at the top of CHANGELOG.md "
                f"before releasing.\n"
                f"   (README's What's New syncs from the TOP entry — a mismatch here silently "
                f"publishes the wrong release notes.)"
            )

        success(f"CHANGELOG has a top-level entry for {self.version}")

    def _released_roadmap_text(self) -> str | None:
        """docs/ROADMAP.yaml as it stood at the last release tag, or None when there is no tag or the file did not exist there."""
        git = dict(capture_output=True, text=True, cwd=str(self.repo_root), check=False)
        tag = subprocess.run(["git", "describe", "--tags", "--abbrev=0"], **git)
        if tag.returncode != 0 or not tag.stdout.strip():
            return None
        shown = subprocess.run(["git", "show", f"{tag.stdout.strip()}:docs/ROADMAP.yaml"], **git)
        return shown.stdout if shown.returncode == 0 else None

    def _garden_roadmap(self, roadmap: dict) -> None:
        """Gardening for the forecast: stop on a silent edit against the last release, warn about every item past its horizon."""
        baseline = self._released_roadmap_text()
        if baseline:
            import yaml

            problems = roadmap_silent_edits(yaml.safe_load(baseline) or {}, roadmap)
            if problems:
                error("docs/ROADMAP.yaml has edits that left no trace:\n  " + "\n  ".join(problems))
        for item in items_past_horizon(roadmap, self.version):
            warning(
                f"roadmap item {item['id']} is past its horizon ({item['horizon']}, this is {self.version}): mark it shipped, dropped, "
                "retracted or superseded, or move the horizon with a revision that says why"
            )

    def _remind_forecast_grading(self, roadmap: dict) -> None:
        """The forecast is only a forecast if it is graded: say how many predictions are still open at every docs step."""
        open_count = sum(1 for i in roadmap["items"] if i["status"] == "predicted")
        if open_count:
            warning(
                f"{open_count} open prediction{'s' if open_count != 1 else ''} in docs/ROADMAP.yaml: grade each against this release "
                "(shipped, dropped, or still predicted) before publishing, or the forecast record stands still"
            )

    def sync_readme_whats_new(self):
        """Sync README 'What's New' section from CHANGELOG.

        Extracts the latest release entry from CHANGELOG.md and replaces
        the What's New section in README.md. This ensures the README
        always reflects the current release content, not just the version number.
        """
        log("\n" + "=" * 60)
        log("📝 Syncing README What's New from CHANGELOG")
        log("=" * 60)

        changelog_path = self.repo_root / "CHANGELOG.md"
        readme_path = self.repo_root / "README.md"

        # Every skip below used to be a warning() + return. A warning in a
        # 200-line release log is invisible: 1.13.4 shipped with README's
        # What's New still reading "What's New in 1.13.3" (bump commit
        # 9051f063b touched only the 5 regex-swept version strings), and
        # nothing failed. A sync that cannot run is a release blocker.
        if not changelog_path.exists() or not readme_path.exists():
            error("CHANGELOG.md or README.md not found — cannot sync README's What's New")

        changelog = changelog_path.read_text()
        try:
            whats_new = render_whats_new(changelog, self.version)
        except ValueError as e:
            error(f"{e}")
        roadmap_path = self.repo_root / "docs" / "ROADMAP.yaml"
        whats_coming = ""
        roadmap = None
        if roadmap_path.exists():
            try:
                roadmap = load_roadmap(roadmap_path)
                whats_coming = render_whats_coming(roadmap)
            except ValueError as e:
                error(f"docs/ROADMAP.yaml: {e}")
            self._garden_roadmap(roadmap)
        elif self._released_roadmap_text():
            # Gardening only ran for a file that exists, so deleting it skipped the no-deletion rule and the README lost
            # What's Coming while this step reported success.
            error(
                "docs/ROADMAP.yaml is missing but the last release had one: every prediction must be retracted, dropped or "
                "superseded in the file, never removed with it"
            )
        new_block = whats_new + ("\n\n" + whats_coming if whats_coming else "")
        readme = readme_path.read_text()
        try:
            readme = replace_whats_new_block(readme, new_block)
        except ValueError as e:
            error(f"README.md: {e}")
        if roadmap is not None and whats_coming:
            self._remind_forecast_grading(roadmap)

        # Built outside the f-strings: a quote or backslash inside an f-string expression is a SyntaxError before Python 3.12.
        coming_note = " and What's Coming from docs/ROADMAP.yaml" if whats_coming else ""
        if self.dry_run:
            info(f"Would sync README What's New{coming_note}")
            return

        readme_path.write_text(readme)

        # Verify the write landed rather than trusting that the regex matched.
        # This is the check whose absence let 1.13.4 ship with a 1.13.3 heading.
        if f"## What's New in {self.version}" not in readme_path.read_text():
            error(
                f"README's What's New still does not read `## What's New in {self.version}` after sync — "
                f"refusing to release a README that advertises a different version than it ships"
            )
        success(f"README What's New synced from CHANGELOG{coming_note}")

    def run_command(self, cmd: list[str], check: bool = True, cwd: str | None = None) -> subprocess.CompletedProcess:
        """Run a shell command"""
        cmd_str = " ".join(cmd)
        cwd_info = f" (in {cwd})" if cwd else ""
        if self.dry_run:
            info(f"Would run: {cmd_str}{cwd_info}")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        info(f"Running: {cmd_str}{cwd_info}")
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, cwd=cwd)
        if result.returncode != 0:
            if result.stderr:
                warning(f"stderr: {result.stderr.strip()}")
            if check:
                error(f"Command failed (exit {result.returncode}): {cmd_str}")
        return result

    def build_package(self):
        """Build Python package"""
        log("\n" + "=" * 60)
        log("📦 Building Python package")
        log("=" * 60)

        # Clean old builds
        for path in ["dist", "build", "empirica.egg-info"]:
            full_path = self.repo_root / path
            if full_path.exists():
                if not self.dry_run:
                    if full_path.is_dir():
                        shutil.rmtree(full_path)
                    else:
                        full_path.unlink()
                    info(f"Removed {path}")

        # Build
        self.run_command(["python3", "-m", "build", "--wheel", "--sdist"], cwd=str(self.repo_root))
        success("Package built successfully")

    def build_mcp_package(self):
        """Build empirica-mcp package"""
        log("\n" + "=" * 60)
        log("📦 Building empirica-mcp package")
        log("=" * 60)

        mcp_dir = self.repo_root / "empirica-mcp"
        if not mcp_dir.exists():
            warning(f"empirica-mcp directory not found: {mcp_dir}")
            return

        # Clean old builds
        for path in ["dist", "build", "empirica_mcp.egg-info"]:
            full_path = mcp_dir / path
            if full_path.exists():
                if not self.dry_run:
                    if full_path.is_dir():
                        shutil.rmtree(full_path)
                    else:
                        full_path.unlink()
                    info(f"Removed empirica-mcp/{path}")

        # Build
        self.run_command(["python3", "-m", "build", "--wheel", "--sdist"], cwd=str(mcp_dir))
        success("empirica-mcp package built successfully")

    def publish_to_pypi(self):
        """Publish to PyPI"""
        log("\n" + "=" * 60)
        log("📤 Publishing to PyPI")
        log("=" * 60)

        if self.dry_run:
            info("Would publish to PyPI using twine")
            return

        self.run_command(["python3", "-m", "twine", "upload", f"dist/empirica-{self.version}*"])
        success(f"Published to PyPI: https://pypi.org/project/empirica/{self.version}/")

    def publish_mcp_to_pypi(self):
        """Publish empirica-mcp to PyPI"""
        log("\n" + "=" * 60)
        log("📤 Publishing empirica-mcp to PyPI")
        log("=" * 60)

        mcp_dir = self.repo_root / "empirica-mcp"
        if not (mcp_dir / "dist").exists():
            warning("empirica-mcp dist/ not found, skipping MCP publish")
            return

        if self.dry_run:
            info("Would publish empirica-mcp to PyPI using twine")
            return

        self.run_command(["python3", "-m", "twine", "upload", str(mcp_dir / "dist" / f"empirica_mcp-{self.version}*")])
        success(f"Published empirica-mcp to PyPI: https://pypi.org/project/empirica-mcp/{self.version}/")

    # Explicit allowlist of files a release commit stages. NEVER `git add -A`:
    # the release runs on the SHARED develop working tree, where other AI
    # sessions may have uncommitted work — a broad add sweeps their edits into
    # the release commit (the 1.12.28 ERM-sweep incident). Both the bump commit
    # (commit_version_bump) and the automated-release commit (create_git_tag)
    # stage exactly this set, so it is a single source of truth (no more
    # "keep in sync with update_version_strings" drift). Keep in sync with the
    # version_files list in update_version_strings when adding a pointer file.
    _VERSION_COMMIT_PATHS = (
        "pyproject.toml",
        "packaging/",
        "Dockerfile",
        "Dockerfile.alpine",
        "README.md",
        "empirica/__init__.py",
        "empirica-mcp/pyproject.toml",
        "empirica-mcp/empirica_mcp/__init__.py",
        "empirica/plugins/claude-code-integration/.claude-plugin/plugin.json",
        "empirica/plugins/claude-code-integration/install.sh",
        "empirica/cli/command_handlers/setup_claude_code.py",
        ".empirica-project/PROJECT_CONFIG.yaml",
        # docs/ current-version pointers (regex-bumped by update_version_strings)
        "docs/README.md",
        "docs/human/developers/EXTENDING_EMPIRICA.md",
        "docs/human/developers/MCP_SERVER_REFERENCE.md",
        "docs/human/end-users/02_INSTALLATION.md",
        # Regenerated by regenerate_cli_docs() during --prepare. Without it here,
        # the committed CLI reference (README links to it) drifts stale every
        # release and leaves an uncommitted edit in the tree.
        "docs/human/developers/CLI_COMMANDS_UNIFIED.md",
    )

    def _git_head(self, cwd=None) -> str:
        """Current HEAD sha in ``cwd`` (default: this repo), or "" if unavailable.

        Takes a cwd because the tap is a DIFFERENT repository and has the same
        false-success shape — a guard that only covered this repo would close the
        instance and leave the class open.
        """
        try:
            out = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(cwd) if cwd else self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
            return (out.stdout or "").strip()
        except Exception:
            return ""

    def _staged_release_paths(self, *extra: str) -> list[str]:
        """The allowlist paths (plus any extras) that actually exist on disk.

        Existence-filtered so a not-yet-created listed file (e.g. a docs pointer
        missing on a partial checkout) doesn't fail the whole ``git add``.
        """
        return [p for p in (*self._VERSION_COMMIT_PATHS, *extra) if (self.repo_root / p).exists()]

    def commit_version_bump(self):
        """Stage + commit ONLY the version-swept files (+ CHANGELOG) for the bump.

        Deliberately NOT ``git add -A``: the release runs on the shared develop
        working tree, so a broad add would sweep a concurrent session's
        uncommitted work into the release commit. Staging the explicit allowlist
        makes that impossible. No-op-safe if nothing changed.
        """
        log("\n" + "=" * 60)
        log("📌 Committing version bump (allowlist only — never `git add -A`)")
        log("=" * 60)
        paths = self._staged_release_paths("CHANGELOG.md")
        if self.dry_run:
            info(f"Would: git add {' '.join(paths)}")
            info(f"Would: git commit -m 'chore(release): bump version to {self.version}'")
            return
        self.run_command(["git", "add", *paths])
        head_before = self._git_head()
        self.run_command(
            ["git", "commit", "-m", f"chore(release): bump version to {self.version}"],
            check=False,  # no-op if nothing to commit
        )
        # ASSERT THE OUTCOME, not the attempt. `check=False` swallows git's
        # "nothing to commit", so this printed the full success banner while HEAD
        # had not moved — hit live cutting 1.13.27, when `--version-only` ran
        # before the pyproject bump and re-swept the CURRENT version onto itself.
        # A false "committed" is expensive here specifically: the next step builds
        # and TAGS whatever is actually on disk.
        head_after = self._git_head()
        if head_after == head_before:
            warning(
                f"NO COMMIT WAS CREATED — HEAD is still {head_before[:8]}. Nothing was staged, which almost "
                f"always means every file already reads {self.version}. Bump the version in pyproject.toml "
                "FIRST (release.py takes the TARGET version from there), then re-run."
            )
            return
        success(
            f"Committed version bump to {self.version} as {head_after[:8]} "
            f"({len(paths)} allowlisted paths, no `git add -A`)"
        )

    def create_git_tag(self):
        """Create and push git tag"""
        log("\n" + "=" * 60)
        log("🏷️  Creating Git tag")
        log("=" * 60)

        tag = f"v{self.version}"

        # Commit ALL release updates (version pointer regex bumps + packaging)
        # via the shared allowlist — never `git add -A` (see _VERSION_COMMIT_PATHS).
        self.run_command(["git", "add", *self._staged_release_paths()])
        self.run_command(
            [
                "git",
                "commit",
                "-m",
                f"chore: automated release {self.version}\n\n"
                f"- Updated all distribution channels\n"
                f"- SHA256: {self.tarball_sha256}",
            ],
            check=False,
        )  # May have no changes

        # Create tag
        self.run_command(["git", "tag", "-a", tag, "-m", f"Release {self.version}"])

        # Push
        self.run_command(["git", "push", "origin", "main", "--tags"])
        success(f"Created and pushed tag: {tag}")

        # Capture the tagged SHA so the post-release "watch CI" line can be keyed
        # to THIS commit. Printing a runnable command matters more than it sounds:
        # the line it replaced was `gh run list --limit 1`, which answers "what is
        # the newest run" rather than "did my commit pass", and those diverge the
        # moment anything else pushes. Best-effort — a missing SHA degrades the
        # hint to a <sha> placeholder rather than failing a published release.
        try:
            self.release_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        except Exception:
            self.release_sha = None

    def build_and_push_docker(self):
        """Build and push Docker images (Debian + Alpine)"""
        log("\n" + "=" * 60)
        log("🐳 Building and pushing Docker images")
        log("=" * 60)

        # Debian image
        debian_tags = [f"nubaeon/empirica:{self.version}", "nubaeon/empirica:latest"]

        build_cmd = ["docker", "build", "."]
        for tag in debian_tags:
            build_cmd.extend(["-t", tag])

        self.run_command(build_cmd, cwd=str(self.repo_root))
        success("Docker image built (Debian)")

        for tag in debian_tags:
            self.run_command(["docker", "push", tag])
            success(f"Pushed: {tag}")

        # Alpine image
        alpine_dockerfile = self.repo_root / "Dockerfile.alpine"
        if alpine_dockerfile.exists():
            alpine_tags = [
                f"nubaeon/empirica:{self.version}-alpine",
            ]

            build_cmd = ["docker", "build", "-f", "Dockerfile.alpine", "."]
            for tag in alpine_tags:
                build_cmd.extend(["-t", tag])

            self.run_command(build_cmd, cwd=str(self.repo_root))
            success("Docker image built (Alpine)")

            for tag in alpine_tags:
                self.run_command(["docker", "push", tag])
                success(f"Pushed: {tag}")
        else:
            warning("Dockerfile.alpine not found, skipping Alpine build")

    def create_github_release(self):
        """Create GitHub release"""
        log("\n" + "=" * 60)
        log("📝 Creating GitHub release")
        log("=" * 60)

        tag = f"v{self.version}"
        wheel = f"dist/empirica-{self.version}-py3-none-any.whl"
        tarball = f"dist/empirica-{self.version}.tar.gz"

        # Include empirica-mcp assets if built
        mcp_wheel = f"empirica-mcp/dist/empirica_mcp-{self.version}-py3-none-any.whl"
        mcp_tarball = f"empirica-mcp/dist/empirica_mcp-{self.version}.tar.gz"
        assets = [wheel, tarball]
        mcp_wheel_path = self.repo_root / mcp_wheel
        mcp_tarball_path = self.repo_root / mcp_tarball
        if mcp_wheel_path.exists():
            assets.append(mcp_wheel)
        if mcp_tarball_path.exists():
            assets.append(mcp_tarball)

        notes = f"""## What's in v{self.version}

See CHANGELOG.md for detailed release notes.

### Installation
```bash
pip install empirica=={self.version}
```

### Docker
```bash
# Security-hardened Alpine (recommended)
docker pull nubaeon/empirica:{self.version}-alpine

# Debian slim
docker pull nubaeon/empirica:{self.version}
```

### Homebrew
```bash
brew tap empiricaai/tap
brew install empirica
```
"""

        # Race tolerance — CI's release workflow may publish first.
        # Try create; on failure check if release already exists and just
        # upload assets. Without this, the script sys.exit's mid-publish and
        # downstream steps (homebrew tap, chocolatey) silently skip.
        # (1.9.6 missed homebrew via exactly this race; 2026-05-17.)
        create_result = self.run_command(
            [
                "gh",
                "release",
                "create",
                tag,
                *assets,
                "--title",
                f"v{self.version}",
                "--notes",
                notes,
            ],
            check=False,
        )

        if create_result.returncode == 0:
            success(f"Created GitHub release: {tag}")
            return

        # Check whether the release exists (CI race) vs a real failure
        view_result = self.run_command(
            ["gh", "release", "view", tag],
            check=False,
        )
        if view_result.returncode == 0:
            warning(f"Release {tag} already exists (likely CI race) — uploading assets with --clobber")
            self.run_command(
                ["gh", "release", "upload", tag, *assets, "--clobber"],
            )
            success(f"Uploaded assets to existing GitHub release: {tag}")
            return

        # Real failure — surface it the way error() does (sys.exit).
        error(f"gh release create failed and release {tag} does not exist: {create_result.stderr.strip()}")

    def run_version_update(self):
        """Update version strings only (no build/publish)."""
        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Version Update                                   ║")
        log("╚════════════════════════════════════════════════════════════╝\n")

        if self.dry_run:
            warning("DRY RUN MODE - No changes will be made\n")

        self.version = self.read_version()

        if not self.old_version:
            error("--old-version required for version-only mode")

        # Targeted regex updates (structural patterns).
        # `update_version_strings` covers every legit current-version pointer
        # explicitly. The naive `sweep_version` catch-all was removed in 1.9.9 —
        # missed patterns get added there, not papered over by a broad replace.
        self.update_version_strings()
        self.update_dockerfile()
        self.update_chocolatey_nuspec()
        self.clear_bytecode_cache()

        success(f"All version strings updated to {self.version}")
        info("Homebrew formula SHA256 will be updated during full release.")

        if self.commit_bump:
            # Commit the sweep here (allowlist only) so callers never need a
            # manual `git add -A` — which on the shared develop tree could sweep
            # a concurrent session's uncommitted work into the release commit.
            self.commit_version_bump()

    def ensure_main_branch(self):
        """Merge develop → main and switch to main for release.

        Release flow: develop (working) → main (release) → tag + publish.
        This avoids homebrew SHA256 conflicts from releasing on develop
        and merging to main afterward.
        """
        log("\n" + "=" * 60)
        log("🔀 Preparing main branch for release")
        log("=" * 60)

        # Check current branch
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=self.repo_root
        )
        current_branch = result.stdout.strip()

        if current_branch == "main":
            info("Already on main branch")
            return

        if current_branch != "develop":
            error(f"Release must be run from 'develop' or 'main', currently on '{current_branch}'")

        # Merge develop → main
        info("Merging develop → main...")
        self.run_command(["git", "checkout", "main"])
        self.run_command(["git", "pull", "origin", "main"], check=False)
        self.run_command(["git", "merge", "develop", "-m", f"Merge develop — Empirica {self.version} release"])
        success("Merged develop → main")

    def back_to_develop(self):
        """Switch back to develop after release and merge any release commits."""
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=self.repo_root
        )
        if result.stdout.strip() == "main":
            info("Switching back to develop...")
            self.run_command(["git", "checkout", "develop"])
            self.run_command(["git", "merge", "main", "-m", f"Merge main — post-release {self.version}"])
            self.run_command(["git", "push", "origin", "develop"], check=False)

    def run_ruff(self) -> bool:
        """Lint gate — mirrors the CI ruff check step.

        Caught 1.9.4 shipping with a leftover unused `import os` that broke
        CI's lint job after the tag was already pushed. Lint failures at
        this stage are cheap to fix; after the tag they cost a re-roll.
        """
        log("\n" + "=" * 60)
        log("🧹 ruff check (lint gate)")
        log("=" * 60)

        if self.dry_run:
            info("Would run: ruff check empirica/ empirica-mcp/ tests/")
            return True

        result = subprocess.run(
            ["ruff", "check", "empirica/", "empirica-mcp/", "tests/"],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(self.repo_root),
        )
        if result.returncode == 0:
            success("ruff clean")
            return True
        log(f"\n{RED}ruff FAILED:{RESET}")
        for line in (result.stdout + result.stderr).strip().splitlines()[-30:]:
            log(f"  {line}")
        return False

    def run_pyright(self) -> bool:
        """Type-check gate — mirrors the CI pyright step."""
        log("\n" + "=" * 60)
        log("🔬 pyright (type-check gate)")
        log("=" * 60)

        if self.dry_run:
            info("Would run: pyright empirica/ empirica-mcp/")
            return True

        result = subprocess.run(
            ["pyright", "empirica/", "empirica-mcp/"],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(self.repo_root),
        )
        if result.returncode == 0:
            success("pyright clean")
            return True
        log(f"\n{RED}pyright FAILED:{RESET}")
        for line in (result.stdout + result.stderr).strip().splitlines()[-30:]:
            log(f"  {line}")
        return False

    def python_floor(self) -> str:
        """The oldest Python the project claims to support, read from `requires-python` so it cannot drift from pyproject."""
        text = (self.repo_root / "pyproject.toml").read_text()
        m = re.search(r'^requires-python\s*=\s*"[^"]*?>=\s*(\d+\.\d+)', text, re.MULTILINE)
        return m.group(1) if m else "3.10"

    def run_python_floor_parse(self, run=subprocess.run) -> bool:
        """Parse the whole tree under the oldest supported interpreter, via uv.

        ruff targets py310 but parses with the newest grammar, and the suite runs on whatever the developer has, so syntax that only
        3.12 accepts passed every local gate and broke CI on 3.11 (1.14.14 follow-up). CI does not run the floor at all. When this cannot
        run it says NOT VERIFIED and returns True: the release is not blocked on a download, but the log cannot read as a clean floor.
        """
        log("\n" + "=" * 60)
        log("🐍 Python-floor parse gate")
        log("=" * 60)
        floor = self.python_floor()
        if self.dry_run:
            info(f"Would parse scripts/ empirica/ tests/ under Python {floor} via uv")
            return True
        if shutil.which("uv") is None:
            warning(
                f"NOT VERIFIED: uv is not installed, so nothing proves the tree parses on Python {floor} (requires-python)"
            )
            return True
        try:
            result = run(
                ["uv", "run", "--python", floor, "--no-project", "python", "-c", PYTHON_FLOOR_PARSE_SCRIPT],
                capture_output=True,
                text=True,
                timeout=600,
                cwd=str(self.repo_root),
            )
        except (OSError, subprocess.TimeoutExpired) as e:
            warning(f"NOT VERIFIED: could not run Python {floor} through uv ({type(e).__name__}: {e})")
            return True
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["no output"]
            warning(f"NOT VERIFIED: uv could not run Python {floor} ({tail[0]})")
            return True
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            warning(f"NOT VERIFIED: the Python {floor} parse printed nothing readable")
            return True
        if not payload["errors"] and not payload.get("checked"):
            warning(f"NOT VERIFIED: the Python {floor} parse walked no files, so it proves nothing about the tree")
            return True
        if payload["errors"]:
            log(f"\n{RED}Python {floor} cannot parse:{RESET}")
            for path, line, msg in payload["errors"][:30]:
                log(f"  {path}:{line}: {msg}")
            return False
        success(f"{payload['checked']} files parse on Python {floor}")
        return True

    # Governed CVE-waiver list. STRICT by default (any un-waived CVE hard-fails).
    # A waiver here is a documented, reviewed risk-acceptance for a CVE that is
    # (a) assessed non-exploitable in Empirica's usage, AND (b) has no available
    # fix. Each MUST carry a rationale and a `retire_when` condition. The gate
    # prints active waivers every run so they stay visible, not hidden. Keep this
    # in sync with `empirica security-audit` (unify: goal — shared waiver source).
    #
    # Sourced from the SHARED governed waiver list
    # (empirica.core.security.waivers.CVE_WAIVERS) so this release gate and
    # `empirica security-audit` can't drift. Currently EMPTY — the sole prior
    # waiver (PYSEC-2026-597, nltk via textstat) was retired in #212 by dropping
    # textstat; nltk is gone from the tree, so the CVE is gone, not waived.
    PIP_AUDIT_WAIVERS: list[dict] = _load_shared_cve_waivers()

    def run_pip_audit(self) -> bool:
        """CVE scan — SCOPED to empirica-managed packages, matching
        ``empirica security-audit`` (finishes #219's gate unification).

        Blocks the release on a CVE in empirica's own dependency surface
        (empirica + its transitive Requires); a CVE in a sibling/user package
        that merely shares the dev venv (empirica-outreach, …) is reported
        informationally, NOT gated — those deps aren't empirica's to ship.
        STRICT within scope except for the governed, documented
        PIP_AUDIT_WAIVERS. Falls back to whole-venv strict when the scope helper
        isn't importable or the output can't be parsed (stricter, never looser)."""
        log("\n" + "=" * 60)
        log("🔒 pip-audit (CVE gate)")
        log("=" * 60)

        ignore_args: list[str] = []
        for w in self.PIP_AUDIT_WAIVERS:
            ignore_args += ["--ignore-vuln", w["id"]]
            warning(f"CVE waiver ACTIVE: {w['id']} ({w['package']}) — {w['rationale']} [retire: {w['retire_when']}]")

        if self.dry_run:
            info(
                f"Would run: pip-audit --skip-editable --format json {' '.join(ignore_args)} (scoped to empirica-managed)"
            )
            return True

        try:
            result = subprocess.run(
                ["pip-audit", "--skip-editable", "--format", "json", *ignore_args],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(self.repo_root),
            )
        except FileNotFoundError:
            warning("pip-audit not installed — skipping CVE gate (install via `pip install pip-audit`)")
            return True  # informational on missing tool; CI is the source of truth

        # Parse JSON findings; if unparseable, fall back to the raw returncode (strict).
        try:
            deps = json.loads(result.stdout or "{}").get("dependencies", [])
        except (ValueError, TypeError):
            if result.returncode == 0:
                success("pip-audit clean (no CVEs)")
                return True
            log(f"\n{RED}pip-audit FAILED (unparseable output — strict):{RESET}")
            for line in (result.stdout + result.stderr).strip().splitlines()[-30:]:
                log(f"  {line}")
            return False

        findings = [(d.get("name", ""), v.get("id", "")) for d in deps for v in (d.get("vulns") or [])]
        if not findings:
            success("pip-audit clean (no CVEs)")
            return True

        # Scope: block only on empirica's managed surface. Fall back to strict-on-all
        # when the scope helper can't be imported (fail-safe, never looser).
        try:
            from empirica.core.security.scope import get_empirica_managed_packages, is_empirica_managed

            managed = get_empirica_managed_packages()
        except Exception:
            managed = set()

        if not managed:
            log(f"\n{RED}pip-audit FAILED (scope unavailable — strict on all {len(findings)} finding(s)):{RESET}")
            for name, vid in findings:
                log(f"  {name}: {vid}")
            return False

        blocking = [(n, v) for n, v in findings if is_empirica_managed(n, managed)]
        informational = [(n, v) for n, v in findings if not is_empirica_managed(n, managed)]

        if informational:
            warning(
                f"{len(informational)} CVE(s) in sibling/user packages sharing the venv — informational, not gated:"
            )
            for name, vid in informational:
                info(f"  {name}: {vid} (not empirica-managed)")

        if blocking:
            log(f"\n{RED}pip-audit FAILED — {len(blocking)} CVE(s) in empirica-managed packages:{RESET}")
            for name, vid in blocking:
                log(f"  {name}: {vid}")
            return False

        extra = f"; {len(informational)} sibling/user CVE(s) ignored" if informational else ""
        success(f"pip-audit clean (no CVEs in empirica-managed surface{extra})")
        return True

    def run_tests(self) -> bool:
        """Run test suite as a release gate. Returns True if tests pass."""
        log("\n" + "=" * 60)
        log("🧪 Running test suite (release gate)")
        log("=" * 60)

        if self.dry_run:
            info("Would run: python3 -m pytest tests/ -x -q --tb=short")
            return True

        # 600s ceiling: full suite is ~3-4min on cold cache. Scanner integration
        # alone can take ~80s — 300s left no headroom and timed out in 1.8.19
        # release prep. Bump gives ~2x safety margin.
        # Deterministic order for the release gate: `-p no:randomly` disables
        # pytest-randomly here so a cut can't coin-flip on the suite's known
        # cross-test isolation debt (a stale module-level cache another test
        # left warm). The release gate must be reproducible. CI keeps random
        # ordering as the watchdog that surfaces that isolation debt — fixing it
        # there is a separate, tracked effort.
        result = subprocess.run(
            [
                "python3",
                "-m",
                "pytest",
                "tests/",
                "-x",
                "-q",
                "--tb=short",
                "--ignore=tests/integration",
                "--ignore=tests/manual_test_goals.py",
                "-p",
                "no:cacheprovider",
                "-p",
                "no:randomly",
            ],
            # Headroom over the real suite runtime (~720s at 1.12.15 and growing);
            # 600s false-timed-out the 1.12.14 release gate. Bump as the suite grows.
            capture_output=True,
            text=True,
            timeout=1200,
            cwd=str(self.repo_root),
        )

        if result.returncode == 0:
            success("Tests passed!")
            if result.stdout:
                # Show summary line
                for line in result.stdout.strip().splitlines()[-3:]:
                    info(f"  {line}")
            return True
        else:
            log(f"\n{RED}Tests FAILED:{RESET}")
            # Show failure output
            output = result.stdout + result.stderr
            for line in output.strip().splitlines()[-20:]:
                log(f"  {line}")
            return False

    def run_import_check(self) -> bool:
        """Quick check that key CLI entry points import without error."""
        log("\n" + "=" * 60)
        log("🔍 Checking critical imports (smoke test)")
        log("=" * 60)

        checks = [
            (
                "session-create",
                "from empirica.cli.command_handlers.session_create import handle_session_create_command",
            ),
            ("cli-core", "from empirica.cli.cli_core import main"),
            ("session-database", "from empirica.data.session_database import SessionDatabase"),
            ("path-resolver", "from empirica.config.path_resolver import get_session_db_path"),
        ]

        all_ok = True
        for name, import_stmt in checks:
            if self.dry_run:
                info(f"Would check: {name}")
                continue
            result = subprocess.run(
                ["python3", "-c", import_stmt],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(self.repo_root),
            )
            if result.returncode == 0:
                success(f"  {name}: OK")
            else:
                log(f"  {RED}{name}: FAILED — {result.stderr.strip().splitlines()[-1]}{RESET}")
                all_ok = False

        return all_ok

    def check_auto_issues(self) -> bool:
        """Check for unresolved high-severity auto-captured issues. Returns True if clean."""
        log("\n" + "=" * 60)
        log("🔎 Checking for unresolved high-severity issues")
        log("=" * 60)

        if self.dry_run:
            info("Would run: empirica issue-list --status new --severity high")
            return True

        try:
            result = subprocess.run(
                ["empirica", "issue-list", "--status", "new", "--severity", "high", "--output", "json"],
                capture_output=True,
                text=True,
                timeout=15,
                cwd=str(self.repo_root),
            )
            if result.returncode != 0:
                warning("Could not check auto-captured issues (command failed). Skipping gate.")
                return True

            import json

            data = json.loads(result.stdout)
            issues = data.get("issues", [])
            if not issues:
                success("No unresolved high-severity issues")
                return True

            log(f"\n{RED}Found {len(issues)} unresolved high-severity issue(s):{RESET}")
            for issue in issues[:10]:
                log(f"  [{issue['id'][:8]}] {issue.get('message', '?')[:100]}")
            if len(issues) > 10:
                log(f"  ... and {len(issues) - 10} more")
            return False

        except (subprocess.TimeoutExpired, FileNotFoundError):
            warning("empirica CLI not available. Skipping auto-issue gate.")
            return True
        except Exception as e:
            warning(f"Auto-issue check failed: {e}. Skipping gate.")
            return True

    def _develop_ci_green(self) -> bool:
        """True iff develop's latest CI run is a completed success for the
        release commit (self.develop_head).

        The full pytest suite is the release gate — but re-running it in
        --prepare duplicates the run develop CI already did on the exact same
        commit (nothing changes between the develop bump and the main merge).
        When that CI is green we can trust it and skip the ~12min re-run (the
        fast gates still run). Best-effort: any gh/parse failure returns False
        so we fall back to running the full suite (safe default).
        """
        if not self.develop_head:
            return False
        try:
            res = subprocess.run(
                [
                    "gh",
                    "run",
                    "list",
                    "--branch",
                    "develop",
                    "--limit",
                    "1",
                    "--json",
                    "headSha,status,conclusion,workflowName",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.repo_root),
            )
            if res.returncode != 0 or not res.stdout.strip():
                return False
            import json as _json

            runs = _json.loads(res.stdout)
            if not runs:
                return False
            r = runs[0]
            return (
                r.get("status") == "completed"
                and r.get("conclusion") == "success"
                and str(r.get("headSha", "")).startswith(self.develop_head[:12])
            )
        except Exception:
            return False

    def run_prepare(self):
        """Prepare release: merge to main, build, test. Does NOT publish.

        This is the safe first half of the release pipeline. After running
        this, review the build artifacts and test results before publishing
        with --publish.
        """
        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Release — PREPARE (merge + build + test)        ║")
        log("╚════════════════════════════════════════════════════════════╝\n")

        if self.dry_run:
            warning("DRY RUN MODE - No changes will be made\n")

        try:
            self.version = self.read_version()

            # Gate, not action. The docs must already be authored and committed
            # on develop — see verify_docs_ready() for why this is a check now.
            # Runs before anything mutates the tree, so a miss aborts on a clean
            # checkout rather than a half-swept one.
            self.verify_docs_ready()

            # Capture develop HEAD BEFORE the merge, so the trust-CI check can
            # match this release commit against develop's CI run.
            head = subprocess.run(
                ["git", "rev-parse", "develop"],
                capture_output=True,
                text=True,
                cwd=str(self.repo_root),
            )
            if head.returncode == 0:
                self.develop_head = head.stdout.strip()

            # Merge develop → main
            if not self.dry_run:
                self.ensure_main_branch()

            # NO writes to tracked docs here. The version sweep, README's What's
            # New and the CLI reference are authored by `--docs` on develop and
            # arrive already committed; verify_docs_ready() above refuses the
            # release otherwise. Doing them here wrote files on main that develop
            # never saw, which is why every release merge conflicted on README.md
            # and CLI_COMMANDS_UNIFIED.md.
            self.clear_bytecode_cache()

            # Build packages
            self.build_package()
            self.build_mcp_package()

            # Calculate SHA256 and update packaging
            self.tarball_sha256 = self.calculate_sha256()
            self.update_homebrew_formula()
            self.update_dockerfile()
            self.update_chocolatey_nuspec()
            self.update_chocolatey_checksum()

            # Gate: import smoke test
            if not self.run_import_check():
                error("Import check failed — fix before publishing.")

            # Gate: ruff (lint) — mirrors CI's ruff check step. Cheap; catches
            # the kind of leftover-import drift that broke v1.9.4's post-tag CI.
            if not self.run_ruff():
                warning("Lint failed. Fix issues before running --publish.")
                warning("You are on the 'main' branch with built artifacts.")
                warning("To abort: git checkout develop && git reset --hard origin/main")
                info("\nOnce fixed, run: python scripts/release.py --publish")
                sys.exit(1)

            # Gate: pyright (types) — mirrors CI's pyright step.
            if not self.run_pyright():
                warning("Type-check failed. Fix issues before running --publish.")
                info("\nOnce fixed, run: python scripts/release.py --publish")
                sys.exit(1)

            # Gate: the tree parses on the oldest supported Python (nothing else runs it).
            if not self.run_python_floor_parse():
                warning("A file does not parse on the Python floor. Fix before running --publish.")
                info("\nOnce fixed, run: python scripts/release.py --publish")
                sys.exit(1)

            # Gate: pip-audit (CVE scan) — mirrors CI's pip-audit step.
            if not self.run_pip_audit():
                warning("CVE scan failed. Fix vulnerabilities before running --publish.")
                info("\nOnce fixed, run: python scripts/release.py --publish")
                sys.exit(1)

            # Gate: test suite. The full pytest suite is the same run develop CI
            # already did on this exact commit — so trust a green develop CI and
            # skip the ~12min re-run (fast gates above still ran). This keeps the
            # release un-reap-prone on environments that can't sustain a long run.
            # Falls through to the full re-run when CI isn't green/unknown.
            if self.skip_tests:
                info("⏭  Skipping the full pytest re-run (--skip-tests) — relying on develop CI for the full suite.")
            elif self._develop_ci_green():
                success(
                    f"develop CI is green for this commit ({self.develop_head[:8]}) — skipping the redundant full-suite re-run."
                )
                info("   (Fast gates import/ruff/pyright/pip-audit ran above; the full suite is CI-verified.)")
            elif not self.run_tests():
                warning("Tests failed. Fix issues before running --publish.")
                warning("You are on the 'main' branch with built artifacts.")
                warning("To abort: git checkout develop && git reset --hard origin/main")
                info("\nOnce fixed, run: python scripts/release.py --publish")
                info("(Or, if develop CI is already green: python scripts/release.py --prepare --skip-tests)")
                sys.exit(1)

            # Gate: no unresolved high-severity auto-captured issues
            if not self.check_auto_issues():
                warning("Unresolved high-severity issues found. Fix or resolve before publishing.")
                warning("Use: empirica issue-list --status new --severity high")
                warning("Resolve with: empirica issue-resolve --session-id <SID> --issue-id <ID> --resolution '...'")
                info("\nOnce resolved, run: python scripts/release.py --publish")
                sys.exit(1)

            log("\n╔════════════════════════════════════════════════════════════╗")
            log("║  ✅ Prepare Complete — Ready to Publish                    ║")
            log("╚════════════════════════════════════════════════════════════╝\n")

            success(f"v{self.version} built and tested on main branch")
            info(f"Artifacts: dist/empirica-{self.version}*.tar.gz, *.whl")
            info(f"SHA256: {self.tarball_sha256}")
            info("\nNext: review changes, then run:")
            info("  python scripts/release.py --publish")

        except Exception as e:
            error(f"Prepare failed: {e}")

    def run_publish(self):
        """Publish a prepared release. Requires --prepare to have been run first."""
        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Release — PUBLISH                               ║")
        log("╚════════════════════════════════════════════════════════════╝\n")

        if self.dry_run:
            warning("DRY RUN MODE - No changes will be made\n")

        try:
            self.version = self.read_version()

            # Re-gate here too: --publish is runnable without --prepare, and
            # publish is the step that makes the notes public.
            self.verify_docs_ready()

            # Verify we're on main with built artifacts
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                cwd=self.repo_root,
            )
            current_branch = result.stdout.strip()
            if current_branch != "main" and not self.dry_run:
                error(f"--publish requires main branch (currently on '{current_branch}'). Run --prepare first.")

            tarball = self.repo_root / "dist" / f"empirica-{self.version}.tar.gz"
            if not tarball.exists() and not self.dry_run:
                error(f"No built artifacts found at {tarball}. Run --prepare first.")

            self.tarball_sha256 = self.calculate_sha256()

            # Publish to all channels
            # The tag IS the publish. Pushing `v*.*.*` to main triggers
            # release.yml, whose jobs cover every channel: build, pypi-empirica,
            # pypi-empirica-mcp, docker, homebrew, github-release.
            #
            # Publishing locally too made this a two-writers-one-artifact race —
            # `a release with the same tag name already exists`, recovered by
            # `--clobber`, three times in one day. CI_CD.md always framed the local
            # path as transitional pending "verified for a release or two"; 1.13.4,
            # 1.13.5 and 1.13.6 all published cleanly through CI, so the handover is
            # due rather than speculative.
            #
            # `--local-artifacts` restores the old path for when CI is unavailable —
            # an escape hatch, not a routine alternative, because running both is
            # exactly what created the race.
            self.create_git_tag()

            # The split is per-channel, because CI's coverage is per-channel.
            #
            # CI publishes PyPI ×2 (OIDC trusted publishing, no secret needed) and
            # the GitHub release (GITHUB_TOKEN). Those are also exactly the channels
            # that raced when we published locally too.
            #
            # Docker moved to CI on 2026-08-05: DOCKERHUB_USERNAME/_TOKEN are now
            # repo secrets, reusing the existing scoped nubaeon registry token
            # rather than minting a new one.
            #
            # HOMEBREW MOVED TO CI (2026-08-19, David's call). The comment that
            # stood here argued Homebrew must stay local because the tap push
            # used the `gh` CLI's own OAuth token, and copying that into a repo
            # secret would widen it across every repo that user can reach. The
            # argument was correct AND its own conclusion has since been acted
            # on: release.yml's `homebrew` job pushes the tap with
            # HOMEBREW_TAP_TOKEN, the fine-grained PAT scoped to
            # EmpiricaAI/homebrew-tap that the comment itself named as the right
            # fix. The local push simply outlived the reason for it.
            #
            # Keeping both was not merely redundant, it was WRONG every time.
            # The local formula carries the sha256 of the sdist built by
            # `--prepare` on this machine, while PyPI serves CI's own build --
            # same source, different bytes, because sdists are not reproducible
            # across builders. Measured: 1.13.24 pushed 2f2491b6 while PyPI
            # served 8e94af5f; 1.13.25 pushed cf389073 while PyPI served
            # b186fa31. CI corrected both a few minutes later, so in between
            # `brew install empirica` failed on a checksum mismatch -- which is
            # indistinguishable from a tampered download. The local push was
            # also what created the tap divergence that had to be reconciled by
            # hand on nearly every release.
            #
            # `update_homebrew_tap()` is kept and still runs under
            # --local-artifacts, the documented escape hatch for when CI is
            # unavailable. Chocolatey no-ops off Windows anyway.
            self.build_and_push_chocolatey()

            if self.local_artifacts:
                warning("--local-artifacts: also publishing PyPI + Docker + GitHub locally — these RACE with CI")
                self.update_homebrew_tap()
                self.publish_to_pypi()
                self.publish_mcp_to_pypi()
                self.build_and_push_docker()
                self.create_github_release()

            # Switch back to develop
            if not self.dry_run:
                self.back_to_develop()

            log("\n╔════════════════════════════════════════════════════════════╗")
            log("║  ✅ Release Published!                                     ║")
            log("╚════════════════════════════════════════════════════════════╝\n")

            success(f"Tagged v{self.version} — CI (release.yml) publishes every channel from here")
            info(
                # By SHA, never by list position. `--limit 1` returns the NEWEST
                # run, which is a different query: if any push starts a run in
                # between, it reports that run's (empty, in_progress) conclusion
                # for the commit you meant to check. Observed printing an empty
                # verdict for a commit that was green.
                "Watch this SHA, not the newest run: "
                "gh run list --branch main --json headSha,status,conclusion "
                f"--jq '.[] | select(.headSha|startswith(\"{self.release_sha[:9]}\"))'"
                if getattr(self, "release_sha", None)
                else "Watch this SHA, not the newest run: gh run list --branch main "
                "--json headSha,status,conclusion --jq '.[] | select(.headSha|startswith(\"<sha>\"))'"
            )
            info(
                f"Verify PyPI on the SIMPLE INDEX — both JSON fields lag: "
                f"curl -s https://pypi.org/simple/empirica/ | grep {self.version}"
            )
            info(f"PyPI: https://pypi.org/project/empirica/{self.version}/")
            info(f"PyPI (MCP): https://pypi.org/project/empirica-mcp/{self.version}/")
            info(f"Docker: docker pull nubaeon/empirica:{self.version}")
            info(f"Docker: docker pull nubaeon/empirica:{self.version}-alpine")
            info(f"GitHub: https://github.com/EmpiricaAI/empirica/releases/tag/v{self.version}")
            info("Homebrew: brew upgrade empirica")
            info("Chocolatey: choco upgrade empirica")
            if not self.report_install_surfaces():
                sys.exit(self.EXIT_BOX_STALE)

        except Exception as e:
            error(f"Publish failed: {e}")

    #: Exit status of --publish when every channel shipped but the box that cut
    #: the release still runs older code through its CLI. Distinct from 1 (a
    #: publish step failed) so a wrapper never retries the publish for it.
    EXIT_BOX_STALE = 3

    def _run_deploy_gap_detector(self) -> list[dict] | None:
        """`empirica doctor --deploy-gaps` from a neutral directory, or None when
        it could not be run or answered nothing — the caller says UNCHECKED."""
        try:
            r = subprocess.run(
                ["empirica", "doctor", "--deploy-gaps", "--output", "json"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                cwd=tempfile.gettempdir(),  # from inside the repo, cwd can masquerade as the install
            )
            raw = r.stdout
            checks = (json.loads(raw[raw.find("{") :]) or {}).get("checks") or []
        except Exception as e:
            warning(f"Could not run the deploy-gap detector ({type(e).__name__}: {e}) — install surfaces UNCHECKED")
            return None
        if not checks:
            warning("Deploy-gap detector returned no checks — install surfaces UNCHECKED")
            return None
        return checks

    @staticmethod
    def _cli_check(checks: list[dict]) -> dict | None:
        return next((c for c in checks if c.get("name") == "CLI matches checkout"), None)

    def _refresh_pipx_cli(self, cli_check: dict) -> bool:
        """Reinstall the pipx CLI editable from this checkout. Only when the
        stale binary IS a pipx install — a system pip is not ours to rewrite.
        Returns True when the reinstall ran and exited 0."""
        pkg_dir = str((cli_check.get("data") or {}).get("cli_package_dir") or "")
        if "pipx" not in pkg_dir:
            info(f"CLI is not a pipx install ({pkg_dir or 'unknown location'}); not refreshing it from here")
            return False
        info("Refreshing the pipx CLI from this checkout: pipx install --force --editable .")
        try:
            r = subprocess.run(
                ["pipx", "install", "--force", "--editable", "."],
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
        except Exception as e:
            warning(f"pipx reinstall did not run ({type(e).__name__}: {e})")
            return False
        if r.returncode != 0:
            warning(f"pipx reinstall exited {r.returncode}: {(r.stderr or r.stdout).strip()[-300:]}")
            return False
        return True

    def report_install_surfaces(self) -> bool:
        """Refresh and verify the box that cut the release; False when it still runs older code.

        This script verifies six PUBLISH channels. Until 4455c5f1a it verified zero
        INSTALL surfaces, and after 1.13.47 that cost an evening: the box kept
        running the previous version through a non-editable pipx CLI, so a correct
        feature was measured as broken by four practices within minutes of
        shipping. The report then only WARNED, and the refresh it named lived in a
        memory file — followed after 1.13.46, skipped after 1.13.47. A memory entry
        is a record, not a guard.

        So publish now does the refresh it used to recommend, for the one surface
        it owns: when the detector says the CLI is a stale pipx copy of THIS
        checkout, it reinstalls it editable and measures again through the
        binary's own interpreter (doctor does that; `python -c import` from inside
        the repo finds ./empirica and always reports the working tree). The
        deployed plugin and the MCP host are not refreshed here — the ecosystem
        update owns them on shared boxes — they are reported, with the hint.

        A CLI still stale after that FAILS the publish (exit EXIT_BOX_STALE): the
        channels shipped, the release is not done. "Could not check" stays
        UNCHECKED, never a pass.
        """
        log("\n" + "=" * 60)
        log("🔎 Install surfaces on THIS box (empirica doctor --deploy-gaps)")
        log("=" * 60)
        checks = self._run_deploy_gap_detector()
        if checks is None:
            return True  # UNCHECKED already said; not a verdict either way

        cli = self._cli_check(checks)
        if cli and cli.get("status") in ("WARN", "FAIL"):
            warning(f"{cli.get('status'):5} {cli.get('name')}: {cli.get('detail', '')}")
            if self._refresh_pipx_cli(cli):
                again = self._run_deploy_gap_detector()
                if again is not None:
                    checks = again
                    cli = self._cli_check(checks)

        gaps = [c for c in checks if c.get("status") in ("WARN", "FAIL")]
        for c in checks:
            line = f"{c.get('status', '?'):5} {c.get('name', '?')}: {c.get('detail', '')}"
            (warning if c in gaps else info)(line)

        cli_stale = bool(cli and cli.get("status") in ("WARN", "FAIL"))
        other = [c for c in gaps if c is not cli]
        if cli_stale:
            warning(
                "This box STILL runs OLDER code than it just released through its CLI — "
                "the publish is not done. Refresh by hand: `pipx install --force --editable .` "
                "(or reinstall however this binary was installed), then re-run "
                "`empirica doctor --deploy-gaps` from /tmp."
            )
        if other:
            warning(
                "Deployed plugin / MCP host lag the release — run your ecosystem update "
                "(or `empirica setup-claude-code` on a box you own)."
            )
        if not gaps:
            success("Install surfaces on this box match the release")
        return not cli_stale

    def run(self):
        """Execute full release process (prepare + publish in one shot).

        For safer releases, use --prepare then --publish separately.
        """
        log("\n╔════════════════════════════════════════════════════════════╗")
        log("║  Empirica Automated Release Pipeline                       ║")
        log("╚════════════════════════════════════════════════════════════╝\n")

        if self.dry_run:
            warning("DRY RUN MODE - No changes will be made\n")

        warning("Running full release (prepare + publish) in one shot.")
        warning("For safer releases, use: --prepare → review → --publish\n")

        try:
            self.version = self.read_version()

            # Merge develop → main
            if not self.dry_run:
                self.ensure_main_branch()

            # Update version strings (targeted regex — `sweep_version`
            # removed in 1.9.9; see comment in clear_bytecode_cache)
            self.update_version_strings()
            self.clear_bytecode_cache()

            # Build packages
            self.build_package()
            self.build_mcp_package()

            # Calculate SHA256 and update packaging
            self.tarball_sha256 = self.calculate_sha256()
            self.update_homebrew_formula()
            self.update_dockerfile()
            self.update_chocolatey_nuspec()
            self.update_chocolatey_checksum()

            # Gate: import smoke test
            if not self.run_import_check():
                error("Import check failed — aborting release.")

            # Gate: test suite
            if not self.run_tests():
                error("Tests failed — aborting release. Fix and retry.")

            # Publish
            # The tag IS the publish. Pushing `v*.*.*` to main triggers
            # release.yml, whose jobs cover every channel: build, pypi-empirica,
            # pypi-empirica-mcp, docker, homebrew, github-release.
            #
            # Publishing locally too made this a two-writers-one-artifact race —
            # `a release with the same tag name already exists`, recovered by
            # `--clobber`, three times in one day. CI_CD.md always framed the local
            # path as transitional pending "verified for a release or two"; 1.13.4,
            # 1.13.5 and 1.13.6 all published cleanly through CI, so the handover is
            # due rather than speculative.
            #
            # `--local-artifacts` restores the old path for when CI is unavailable —
            # an escape hatch, not a routine alternative, because running both is
            # exactly what created the race.
            self.create_git_tag()

            # The split is per-channel, because CI's coverage is per-channel.
            #
            # CI publishes PyPI ×2 (OIDC trusted publishing, no secret needed) and
            # the GitHub release (GITHUB_TOKEN). Those are also exactly the channels
            # that raced when we published locally too.
            #
            # Docker moved to CI on 2026-08-05: DOCKERHUB_USERNAME/_TOKEN are now
            # repo secrets, reusing the existing scoped nubaeon registry token
            # rather than minting a new one.
            #
            # HOMEBREW MOVED TO CI (2026-08-19, David's call). The comment that
            # stood here argued Homebrew must stay local because the tap push
            # used the `gh` CLI's own OAuth token, and copying that into a repo
            # secret would widen it across every repo that user can reach. The
            # argument was correct AND its own conclusion has since been acted
            # on: release.yml's `homebrew` job pushes the tap with
            # HOMEBREW_TAP_TOKEN, the fine-grained PAT scoped to
            # EmpiricaAI/homebrew-tap that the comment itself named as the right
            # fix. The local push simply outlived the reason for it.
            #
            # Keeping both was not merely redundant, it was WRONG every time.
            # The local formula carries the sha256 of the sdist built by
            # `--prepare` on this machine, while PyPI serves CI's own build --
            # same source, different bytes, because sdists are not reproducible
            # across builders. Measured: 1.13.24 pushed 2f2491b6 while PyPI
            # served 8e94af5f; 1.13.25 pushed cf389073 while PyPI served
            # b186fa31. CI corrected both a few minutes later, so in between
            # `brew install empirica` failed on a checksum mismatch -- which is
            # indistinguishable from a tampered download. The local push was
            # also what created the tap divergence that had to be reconciled by
            # hand on nearly every release.
            #
            # `update_homebrew_tap()` is kept and still runs under
            # --local-artifacts, the documented escape hatch for when CI is
            # unavailable. Chocolatey no-ops off Windows anyway.
            self.build_and_push_chocolatey()

            if self.local_artifacts:
                warning("--local-artifacts: also publishing PyPI + Docker + GitHub locally — these RACE with CI")
                self.update_homebrew_tap()
                self.publish_to_pypi()
                self.publish_mcp_to_pypi()
                self.build_and_push_docker()
                self.create_github_release()

            # Switch back to develop
            if not self.dry_run:
                self.back_to_develop()

            log("\n╔════════════════════════════════════════════════════════════╗")
            log("║  ✅ Release Complete!                                      ║")
            log("╚════════════════════════════════════════════════════════════╝\n")

            success(f"Tagged v{self.version} — CI (release.yml) publishes every channel from here")
            info(
                # By SHA, never by list position. `--limit 1` returns the NEWEST
                # run, which is a different query: if any push starts a run in
                # between, it reports that run's (empty, in_progress) conclusion
                # for the commit you meant to check. Observed printing an empty
                # verdict for a commit that was green.
                "Watch this SHA, not the newest run: "
                "gh run list --branch main --json headSha,status,conclusion "
                f"--jq '.[] | select(.headSha|startswith(\"{self.release_sha[:9]}\"))'"
                if getattr(self, "release_sha", None)
                else "Watch this SHA, not the newest run: gh run list --branch main "
                "--json headSha,status,conclusion --jq '.[] | select(.headSha|startswith(\"<sha>\"))'"
            )
            info(
                f"Verify PyPI on the SIMPLE INDEX — both JSON fields lag: "
                f"curl -s https://pypi.org/simple/empirica/ | grep {self.version}"
            )
            info(f"PyPI: https://pypi.org/project/empirica/{self.version}/")
            info(f"PyPI (MCP): https://pypi.org/project/empirica-mcp/{self.version}/")
            info(f"Docker: docker pull nubaeon/empirica:{self.version}")
            info(f"Docker: docker pull nubaeon/empirica:{self.version}-alpine")
            info(f"GitHub: https://github.com/EmpiricaAI/empirica/releases/tag/v{self.version}")
            info("Homebrew: brew upgrade empirica")
            info("Chocolatey: choco upgrade empirica")

        except Exception as e:
            error(f"Release failed: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Automated release script for Empirica",
        epilog="""
Recommended flow:
  1. python scripts/release.py --prepare          # merge, build, test
  2. (review artifacts, smoke test manually)
  3. python scripts/release.py --publish           # push to all channels

Legacy (one-shot, less safe):
  python scripts/release.py                        # prepare + publish
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without executing")
    parser.add_argument("--old-version", help="Previous version for broad sweep replacement (e.g. 1.5.6)")
    parser.add_argument(
        "--version-only",
        action="store_true",
        help="Update version strings only (no build/publish). Requires --old-version.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help=(
            "With --version-only, commit the bump too — staging ONLY the version/"
            "packaging allowlist + CHANGELOG.md (never `git add -A`, so a concurrent "
            "session's uncommitted work can't be swept into the release commit)."
        ),
    )
    parser.add_argument(
        "--local-artifacts",
        action="store_true",
        help=(
            "With --publish, ALSO publish artifacts from this machine (PyPI, Docker, "
            "GitHub release, Homebrew). Default is tag-and-push only — the tag triggers "
            "release.yml, which owns every channel. Escape hatch for when CI is down; "
            "running both races on the GitHub release."
        ),
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help=(
            "Check that the CURRENT version actually landed on every channel — "
            "artifacts, not CI job status. Run a few minutes after --publish."
        ),
    )
    parser.add_argument(
        "--docs",
        action="store_true",
        help=(
            "Author the release-facing docs on the CURRENT branch (develop): version "
            "sweep, README What's New from CHANGELOG, CLI reference regen. Writes "
            "nothing to git — review the diff and commit it with the bump. --prepare "
            "then only VERIFIES these are done."
        ),
    )
    parser.add_argument(
        "--prepare",
        action="store_true",
        help="Merge to main, build, and test — but do NOT publish. Review before --publish.",
    )
    parser.add_argument(
        "--publish", action="store_true", help="Publish a prepared release (requires --prepare to have been run first)."
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help=(
            "In --prepare, skip the full pytest re-run and rely on develop CI's green "
            "run for the full suite. The fast gates (import/ruff/pyright/pip-audit) still "
            "run. --prepare ALSO auto-skips the full re-run when develop CI is already "
            "green for the release commit; this flag forces the skip even if CI status "
            "can't be read."
        ),
    )
    args = parser.parse_args()

    if args.prepare and args.publish:
        parser.error("Use --prepare and --publish separately, not together.")

    if args.commit and not args.version_only:
        parser.error("--commit is only valid with --version-only.")

    manager = ReleaseManager(
        local_artifacts=args.local_artifacts,
        dry_run=args.dry_run,
        old_version=args.old_version,
        skip_tests=args.skip_tests,
        commit_bump=args.commit,
    )
    if args.verify:
        manager.run_verify()
    elif args.docs:
        manager.run_docs()
    elif args.version_only:
        manager.run_version_update()
    elif args.prepare:
        manager.run_prepare()
    elif args.publish:
        manager.run_publish()
    else:
        manager.run()


if __name__ == "__main__":
    main()

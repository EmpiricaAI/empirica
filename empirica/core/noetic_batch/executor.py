"""Executor — fulfils each operation type, returns merged structured response.

Per-op errors are captured (each result has its own `error` field); the
batch as a whole only fails on input schema validation. Budgets are
enforced per-op AND total — when a cap hits, `truncated` flags fire.
"""

from __future__ import annotations

import glob as _glob
import logging
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .budgets import BatchBudgets
from .schema import (
    BatchSummary,
    GlobOperation,
    GlobResult,
    GrepMatch,
    GrepOperation,
    GrepResult,
    InvestigateOperation,
    InvestigateResult,
    NoeticBatchInput,
    NoeticBatchResult,
    ReadOperation,
    ReadResult,
)

logger = logging.getLogger(__name__)

GREP_TIMEOUT_SECONDS = 30
INVESTIGATE_TIMEOUT_SECONDS = 30


def _resolve_project_root(explicit: Path | None) -> Path:
    """Resolve the batch project root. See ``run_batch`` for priority chain."""
    if explicit is not None:
        return explicit.resolve()
    try:
        from empirica.utils.session_resolver import InstanceResolver

        resolved = InstanceResolver.project_path()
        if resolved:
            return Path(resolved).resolve()
    except Exception:  # noqa: S110 — resolver failure is recoverable, fall back to cwd
        pass
    return Path.cwd().resolve()


def run_batch(
    payload: dict,
    *,
    project_root: Path | None = None,
    budgets: BatchBudgets | None = None,
) -> NoeticBatchResult:
    """Validate input, fulfil each operation, return merged result.

    Schema-validation errors raise pydantic ValidationError to the caller
    (the CLI wrapper renders them as `ok: false`). Per-op errors are
    captured in each result.

    Project-root resolution priority:
        1. Explicit ``project_root`` arg (CLI passes one already resolved)
        2. ``InstanceResolver.project_path()`` — canonical Empirica resolver
        3. ``Path.cwd()`` — last-resort fallback
    Step 2 is the substrate fix for the cross-project investigation bug:
    when invoked from a CWD that isn't the empirica project, grep ops
    were silently scoped to the wrong tree.
    """
    started = time.time()
    parsed = NoeticBatchInput(**payload)
    project_root = _resolve_project_root(project_root)
    budgets = budgets or BatchBudgets()

    result = NoeticBatchResult(intent=parsed.intent)

    for op in parsed.reads:
        result.reads.append(_execute_read(op, project_root, budgets))
    for op in parsed.greps:
        result.greps.append(_execute_grep(op, project_root, budgets))
    for op in parsed.globs:
        # _normalize_globs in schema converts strings → GlobOperation
        result.globs.append(_execute_glob(op, project_root, budgets))  # type: ignore[arg-type]
    for op in parsed.investigate:
        result.investigate.append(_execute_investigate(op, project_root, budgets))

    result.summary = _build_summary(result, started)

    # Misuse signal — calling noetic-batch with a single op is the
    # tell-tale "I'm using this as a Sentinel bypass" pattern. Don't
    # reject (might be legit edge case), but surface a warning so
    # downstream tooling and the operator can see it.
    op_count = parsed.operation_count()
    if op_count < 2:
        result.warning = (
            "noetic-batch called with a single operation — this is misuse. "
            "Individual Read/Grep/Glob/investigate are noetic in any phase "
            "and don't need batching. The batch tool exists for grouping "
            "≥3 investigation operations into one merged response."
        )

    # Visibility breadcrumb on stderr — observers (humans, logs) can see
    # what the batch returned without parsing the full JSON. Stays out
    # of stdout so JSON consumers aren't affected.
    summary = result.summary
    if summary is not None:
        try:
            sys.stderr.write(
                f"[noetic-batch] intent={parsed.intent!r:.80s} "
                f"reads={summary.total_files_read} "
                f"grep_matches={summary.total_grep_matches} "
                f"globs={summary.total_globs_resolved} "
                f"investigate={summary.total_investigate_results} "
                f"~tokens={summary.approx_tokens} "
                f"duration={summary.duration_ms}ms\n"
            )
        except Exception:  # noqa: S110 — telemetry breadcrumb, must not fail the batch
            pass

    return result


# =============================================================================
# Per-operation executors
# =============================================================================


def _execute_read(op: ReadOperation, project_root: Path, budgets: BatchBudgets) -> ReadResult:
    """Read a file with optional line-range slicing and byte cap."""
    target = (project_root / op.path).resolve() if not Path(op.path).is_absolute() else Path(op.path)
    if not target.exists():
        return ReadResult(path=op.path, lines=op.lines, error=f"file not found: {op.path}")
    if not target.is_file():
        return ReadResult(path=op.path, lines=op.lines, error=f"not a regular file: {op.path}")

    try:
        data = target.read_bytes()
    except OSError as exc:
        return ReadResult(path=op.path, lines=op.lines, error=f"read failed: {exc}")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ReadResult(
            path=op.path,
            lines=op.lines,
            error="binary or non-UTF-8 file (use Read tool with explicit encoding instead)",
        )

    sliced, slice_err = _slice_lines(text, op.lines)
    if slice_err:
        return ReadResult(path=op.path, lines=op.lines, error=slice_err)

    truncated = False
    encoded = sliced.encode("utf-8")
    if len(encoded) > budgets.max_file_bytes:
        sliced = encoded[: budgets.max_file_bytes].decode("utf-8", errors="ignore")
        truncated = True

    return ReadResult(
        path=op.path,
        lines=op.lines,
        content=sliced,
        size_bytes=len(sliced.encode("utf-8")),
        truncated=truncated,
    )


def _slice_lines(text: str, spec: str | None) -> tuple[str, str | None]:
    """Apply a 'N-M' / 'N-' / '-M' / 'N' line range to text."""
    if spec is None:
        return text, None
    spec = spec.strip()
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if "-" in spec:
        start_str, end_str = spec.split("-", 1)
        start = int(start_str) if start_str else 1
        end = int(end_str) if end_str else total
    else:
        start = end = int(spec)
    start = max(1, min(start, total))
    end = max(start, min(end, total))
    return "".join(lines[start - 1 : end]), None


def _execute_grep(op: GrepOperation, project_root: Path, budgets: BatchBudgets) -> GrepResult:
    """Run grep across files matching the glob. Prefers ripgrep, falls back to Python."""
    started = time.time()
    cap = min(op.max_matches, budgets.max_grep_matches)
    grep_root = Path(op.root).resolve() if op.root else project_root
    if not grep_root.exists():
        return GrepResult(pattern=op.pattern, glob=op.glob, error=f"root does not exist: {grep_root}")
    rg = shutil.which("rg")
    if rg:
        return _execute_grep_rg(op, grep_root, rg, cap, started)
    return _execute_grep_python(op, grep_root, cap, started)


def _execute_grep_rg(
    op: GrepOperation,
    grep_root: Path,
    rg_path: str,
    cap: int,
    started: float,
) -> GrepResult:
    """ripgrep-backed grep — fast path."""
    # Ask rg for one MORE than the cap so truncation is detectable: the loop
    # below stops at the (cap+1)th match and flags truncated. With --max-count
    # cap, rg emits exactly cap and the over-cap signal never arrives → a capped
    # result is silently reported as complete.
    cmd = [rg_path, "--json", "--max-count", str(cap + 1)]
    if not op.case_sensitive:
        cmd.append("--ignore-case")
    if op.context > 0:
        cmd += ["--context", str(op.context)]
    # Search "." from inside grep_root rather than passing the absolute root as
    # the path arg. ripgrep 15+ anchors a multi-component --glob (e.g.
    # "src/**/*.py") against the FULL path it's handed, so an absolute root never
    # matches a root-relative glob → 0 files. Running with cwd=grep_root keeps the
    # glob root-relative across rg versions; paths come back relative to grep_root.
    cmd += ["--glob", op.glob, op.pattern, "."]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=GREP_TIMEOUT_SECONDS,
            cwd=str(grep_root),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return GrepResult(pattern=op.pattern, glob=op.glob, error="grep timed out")

    matches: list[GrepMatch] = []
    files_scanned = 0
    truncated = False
    pending_before: list[str] = []

    import json as _json

    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            entry = _json.loads(line)
        except _json.JSONDecodeError:
            continue
        kind = entry.get("type")
        data = entry.get("data", {})
        if kind == "begin":
            files_scanned += 1
            pending_before = []
        elif kind == "context" and op.context > 0:
            pending_before.append(_extract_text(data))
            if len(pending_before) > op.context:
                pending_before = pending_before[-op.context :]
        elif kind == "match":
            if len(matches) >= cap:
                truncated = True
                break
            file_path = _extract_path(data)
            line_num = data.get("line_number", 0)
            text = _extract_text(data)
            matches.append(
                GrepMatch(
                    # rg now returns paths relative to grep_root (cwd); absolutize
                    # against grep_root before normalizing so the contract holds.
                    file=_relative_or_abs(str(grep_root / file_path), grep_root) if file_path else "",
                    line=line_num,
                    text=text.rstrip("\n"),
                    context_before=list(pending_before) if op.context > 0 else [],
                )
            )
            pending_before = []

    return GrepResult(
        pattern=op.pattern,
        glob=op.glob,
        matches=matches,
        total_matches=len(matches),
        truncated=truncated,
        files_scanned=files_scanned,
        duration_ms=int((time.time() - started) * 1000),
    )


def _extract_text(data: dict) -> str:
    """ripgrep JSON nests text under 'lines' or 'submatches'."""
    lines = data.get("lines", {})
    if isinstance(lines, dict):
        return lines.get("text", "")
    return ""


def _extract_path(data: dict) -> str:
    path = data.get("path", {})
    if isinstance(path, dict):
        return path.get("text", "")
    return str(path) if path else ""


def _relative_or_abs(file_path: str, root: Path) -> str:
    """Return path relative to root, or the absolute path if outside root."""
    try:
        return str(Path(file_path).relative_to(root))
    except ValueError:
        return str(Path(file_path).resolve())


def _execute_grep_python(op: GrepOperation, grep_root: Path, cap: int, started: float) -> GrepResult:
    """Pure-Python grep — fallback when rg isn't available."""
    flags = 0 if op.case_sensitive else re.IGNORECASE
    try:
        regex = re.compile(op.pattern, flags)
    except re.error as exc:
        return GrepResult(pattern=op.pattern, glob=op.glob, error=f"invalid regex: {exc}")

    matches: list[GrepMatch] = []
    files_scanned = 0
    truncated = False

    for file_path in _resolve_glob(grep_root, op.glob, max_files=10000):
        files_scanned += 1
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if regex.search(line):
                if len(matches) >= cap:
                    truncated = True
                    break
                ctx_before = lines[max(0, i - op.context) : i] if op.context else []
                ctx_after = lines[i + 1 : i + 1 + op.context] if op.context else []
                matches.append(
                    GrepMatch(
                        file=_relative_or_abs(str(file_path), grep_root),
                        line=i + 1,
                        text=line,
                        context_before=ctx_before,
                        context_after=ctx_after,
                    )
                )
        if truncated:
            break

    return GrepResult(
        pattern=op.pattern,
        glob=op.glob,
        matches=matches,
        total_matches=len(matches),
        truncated=truncated,
        files_scanned=files_scanned,
        duration_ms=int((time.time() - started) * 1000),
    )


def _execute_glob(op: GlobOperation, project_root: Path, budgets: BatchBudgets) -> GlobResult:
    """Resolve a glob pattern to a file list."""
    root = Path(op.root).resolve() if op.root else project_root
    if not root.exists():
        return GlobResult(pattern=op.pattern, error=f"root does not exist: {root}")

    matches: list[str] = []
    truncated = False
    for path in _resolve_glob(root, op.pattern, max_files=budgets.max_glob_files + 1):
        if len(matches) >= budgets.max_glob_files:
            truncated = True
            break
        try:
            matches.append(str(path.relative_to(root)))
        except ValueError:
            matches.append(str(path))

    return GlobResult(
        pattern=op.pattern,
        matches=matches,
        total_matches=len(matches),
        truncated=truncated,
    )


def _resolve_glob(root: Path, pattern: str, max_files: int):
    """Yield up to max_files Path objects matching `pattern` relative to root."""
    base = str(root)
    full_pattern = str(root / pattern)
    count = 0
    for raw in _glob.iglob(full_pattern, recursive=True):
        path = Path(raw)
        if not path.is_file():
            continue
        try:
            path.relative_to(base)
        except ValueError:
            continue
        yield path
        count += 1
        if count >= max_files:
            return


def _execute_investigate(
    op: InvestigateOperation,
    project_root: Path,
    budgets: BatchBudgets,
) -> InvestigateResult:
    """Semantic search via empirica project-search CLI."""
    cap = min(op.limit, budgets.max_investigate_results)
    cmd = [
        "empirica",
        "project-search",
        "--task",
        op.query,
        "--limit",
        str(cap),
        "--output",
        "json",
    ]
    if op.scope == "global":
        cmd.append("--global")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=INVESTIGATE_TIMEOUT_SECONDS,
            cwd=str(project_root),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return InvestigateResult(query=op.query, scope=op.scope, error="investigate timed out")
    except FileNotFoundError:
        return InvestigateResult(query=op.query, scope=op.scope, error="empirica CLI not found in PATH")

    if not proc.stdout.strip():
        return InvestigateResult(
            query=op.query, scope=op.scope, error=f"empty result (stderr: {proc.stderr.strip()[:200]})"
        )

    import json as _json

    try:
        payload = _json.loads(proc.stdout)
    except _json.JSONDecodeError:
        return InvestigateResult(query=op.query, scope=op.scope, error="non-JSON response from project-search")

    # project-search returns variable shape; normalize to a list of dicts
    if isinstance(payload, list):
        results = payload
    elif isinstance(payload, dict):
        results = payload.get("results") or payload.get("matches") or [payload]
    else:
        results = []

    truncated = len(results) > cap
    return InvestigateResult(
        query=op.query,
        scope=op.scope,
        results=results[:cap],
        truncated=truncated,
    )


# =============================================================================
# Summary / total budgeting
# =============================================================================


def _build_summary(result: NoeticBatchResult, started: float) -> BatchSummary:
    total_read = sum(1 for r in result.reads if not r.error)
    total_matches = sum(g.total_matches for g in result.greps)
    total_globs = sum(g.total_matches for g in result.globs)
    total_invest = sum(len(i.results) for i in result.investigate)

    # rough token estimate (4 chars ≈ 1 token)
    approx_bytes = (
        sum(r.size_bytes for r in result.reads)
        + sum(len(m.text) + sum(len(c) for c in m.context_before) for g in result.greps for m in g.matches)
        + sum(sum(len(p) for p in g.matches) for g in result.globs)
    )

    return BatchSummary(
        total_files_read=total_read,
        total_grep_matches=total_matches,
        total_globs_resolved=total_globs,
        total_investigate_results=total_invest,
        duration_ms=int((time.time() - started) * 1000),
        approx_tokens=approx_bytes // 4,
    )

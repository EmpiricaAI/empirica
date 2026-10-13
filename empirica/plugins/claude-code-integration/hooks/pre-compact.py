#!/usr/bin/env python3
"""
Empirica PreCompact Hook - Unified context capture before memory compacting

This hook runs automatically before Claude Code compacts the conversation.
It captures:
1. CANONICAL vectors via assess-state (fresh self-assessment)
2. Context anchor via project-bootstrap (findings, unknowns, goals)
3. Last human task from transcript (for continuity)
4. Git context (branch, modified files, recent commits)

Writes a UNIFIED breadcrumbs git note combining task context + epistemic state.
(Previously split across breadcrumbs bash scripts and empirica Python hooks.)
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Import shared utilities from plugin lib
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from project_resolver import _get_instance_suffix, find_project_root, get_instance_id

# Patterns that indicate a message is system-injected, not human input
_SKIP_PATTERNS = [
    "<command-name>",  # Skill tool injections
    "<system-reminder>",  # System reminders
    "Base directory for this skill:",  # Skill headers
    "[Request interrupted by user]",
    "[Request interrupted by user for tool use]",
]


#: How much of the transcript's tail to scan for the last human message. The
#: message is always near the end; the whole file is not needed and cannot be
#: afforded: this session's transcript reached 1.94 GB and `read_text()` on it
#: took 27.9 s of the hook's 30 s budget, so the harness killed pre-compact
#: before its snapshot write on every compaction for 48 days. 4 MB covers
#: thousands of entries, including the multi-hundred-KB tool results that sit
#: between human turns.
TRANSCRIPT_TAIL_BYTES = 4 * 1024 * 1024


def _read_transcript_tail(transcript_path: str, tail_bytes: int = TRANSCRIPT_TAIL_BYTES) -> list[str]:
    """The last ``tail_bytes`` of the transcript as complete lines (a partial
    first line from cutting mid-record is dropped)."""
    path = Path(transcript_path)
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > tail_bytes:
            f.seek(size - tail_bytes)
            chunk = f.read()
            # The first line is almost certainly cut mid-record; drop it.
            chunk = chunk.split(b"\n", 1)[1] if b"\n" in chunk else b""
        else:
            chunk = f.read()
    return chunk.decode("utf-8", errors="replace").strip().split("\n")


def _extract_last_task(transcript_path: str, max_chars: int = 500) -> str:
    """Extract the last human task message from the JSONL transcript.

    Reads only the tail of the file — see TRANSCRIPT_TAIL_BYTES for why.

    Filters out:
    - Tool results (content is array, not string)
    - Skill injections (<command-name> tags)
    - System reminders
    - Very long messages (>2000 chars = likely injected content)
    """
    if not transcript_path or not Path(transcript_path).exists():
        return ""

    try:
        lines = _read_transcript_tail(transcript_path)
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get("type") != "user":
                continue

            message = entry.get("message", {})
            content = message.get("content", "")

            # Content must be a string (array = tool result)
            if not isinstance(content, str):
                continue

            content = content.strip()
            if not content:
                continue

            # Skip known non-human patterns
            skip = False
            for pattern in _SKIP_PATTERNS:
                if pattern in content[:300]:
                    skip = True
                    break
            if skip:
                continue

            # Skip very long messages (injected content, not human input)
            if len(content) > 2000:
                continue

            return content[:max_chars]
    except Exception:
        pass

    return ""


def _gather_git_context(num_commits: int = 5) -> dict:
    """Gather git context: branch, modified files, recent commits."""
    context = {"branch": "", "modified_files": "", "recent_commits": ""}

    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"], capture_output=True, text=True, timeout=5, cwd=os.getcwd()
        )
        context["branch"] = result.stdout.strip() or "detached"
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5, cwd=os.getcwd()
        )
        files = result.stdout.strip()
        if files:
            lines = files.split("\n")[:20]
            context["modified_files"] = "\n".join(f"  {l}" for l in lines)
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "log", "--oneline", f"-{num_commits}"], capture_output=True, text=True, timeout=5, cwd=os.getcwd()
        )
        commits = result.stdout.strip()
        if commits:
            context["recent_commits"] = "\n".join(f"  {l}" for l in commits.split("\n"))
    except Exception:
        pass

    return context


def _write_unified_breadcrumbs_note(
    session_id: str,
    timestamp: str,
    vectors: dict,
    snapshot_filename: str,
    breadcrumbs_summary: dict,
    last_task: str,
    git_context: dict,
) -> bool:
    """Write unified breadcrumbs git note combining task context + epistemic state.

    Single note on 'breadcrumbs' ref replaces the old dual-system approach
    (separate bash breadcrumbs + empirica-precompact notes).
    """
    know = vectors.get("know", "N/A")
    uncertainty = vectors.get("uncertainty", "N/A")
    completion = vectors.get("completion", "N/A")
    context_v = vectors.get("context", "N/A")

    branch = git_context.get("branch", "unknown")
    modified = git_context.get("modified_files", "") or "[Working tree clean]"
    commits = git_context.get("recent_commits", "") or "[No recent commits]"

    note = f"""🍞 BREADCRUMBS - {timestamp}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BRANCH: {branch}

LAST_TASK:
{last_task or "[Could not extract from transcript]"}

MODIFIED_FILES:
{modified}

RECENT_COMMITS:
{commits}

🧠 EPISTEMIC STATE
Session: {session_id}
Vectors: know={know}, uncertainty={uncertainty}, completion={completion}, context={context_v}
Snapshot: {snapshot_filename}

Artifacts: {breadcrumbs_summary.get("findings_count", 0)} findings, {breadcrumbs_summary.get("unknowns_count", 0)} unknowns, {breadcrumbs_summary.get("goals_count", 0)} goals, {breadcrumbs_summary.get("dead_ends_count", 0)} dead-ends

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RETRIEVAL:
  empirica project-bootstrap --session-id {session_id[:8]}...
  empirica project-search --task "<what you need>"
  cat .empirica/ref-docs/{snapshot_filename}
"""

    try:
        done = subprocess.run(
            ["git", "notes", "--ref=breadcrumbs", "add", "-f", "-F", "-", "HEAD"],
            input=note,
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.getcwd(),
        )
        # The caller renders this as a tick. Without reading returncode, a
        # refused write (no HEAD, a notes lock, a git error) printed the tick
        # and lost the breadcrumb: deleting this call entirely would have looked
        # identical.
        if done.returncode != 0:
            print(f"breadcrumbs note refused: {done.stderr.strip()[:200]}", file=sys.stderr)
            return False
        return True
    except Exception:
        return False


def _write_compact_handoff(project_root, claude_session_id):
    """Write compact handoff file for post-compact to read.

    Ensures post-compact resolves to the same project even if
    active_work/instance_projects get stale between pre and post compact.
    """
    try:
        instance_id = get_instance_id()
        handoff_suffix = _get_instance_suffix()
        handoff_file = Path.home() / ".empirica" / f"compact_handoff{handoff_suffix}.json"
        handoff_data = {
            "project_path": str(project_root),
            "claude_session_id": claude_session_id,
            "instance_id": instance_id,
            "timestamp": datetime.now().isoformat(),
        }
        with open(handoff_file, "w") as f:
            json.dump(handoff_data, f, indent=2)
    except Exception:
        pass  # Handoff write failure is non-fatal


def _snapshot_identity(claude_session_id, empirica_session) -> dict:
    """Who this snapshot belongs to, so post-compact restores its own and not whichever was written last."""
    return {"claude_session_id": claude_session_id, "empirica_session_id": empirica_session}


def _detect_empirica_session(claude_session_id=None):
    """Detect this compaction's Empirica session.

    The claude session that is compacting has an active_work file naming its Empirica session: that is identity.
    Only without it does this fall back to the latest active session for the ai_id, which is recency across
    every instance in the project and can be a sibling's.

    Returns session ID string or None.
    """
    if claude_session_id:
        try:
            with open(Path.home() / ".empirica" / f"active_work_{claude_session_id}.json") as f:
                own = json.load(f).get("empirica_session_id")
            if own:
                return own
        except Exception:
            pass
    try:
        from empirica.utils.session_resolver import InstanceResolver as R

        # Try the canonical ai_id first (project.yaml → basename), then the
        # legacy 'claude-code' literal, then the None wildcard. Prepend the
        # resolved id ONLY when truthy — a leading None would short-circuit
        # to the wildcard and skip the legacy-pattern lookup.
        resolved = R.ai_id()
        ai_patterns = ([resolved] if resolved else []) + ["claude-code", None]
        for ai_pattern in ai_patterns:
            try:
                return R.latest_session_id(ai_id=ai_pattern, active_only=True)
            except ValueError:
                continue
    except Exception:
        pass
    return None


STASH_TAG = "empirica: pre-compact"


def _recover_orphaned_stashes():
    """Restore a stash a PREVIOUS hook run pushed and never popped.

    The pop can be skipped in one way that no amount of care inside this
    process prevents: the harness kills the hook at its timeout budget, and
    SIGKILL runs no handler. Recovery therefore cannot live only in the run
    that created the stash — the NEXT run has to find it.

    Only the topmost entry is considered, and only when the working tree is
    clean, so this can never overwrite live work or reorder a user's own
    stashes. Returns the restored stash's subject, or None.
    """
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=os.getcwd(), capture_output=True, text=True, timeout=5
        )
        if status.stdout.strip():
            return None
        listing = subprocess.run(
            ["git", "stash", "list", "--format=%gd\t%s", "-1"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5,
        )
        top = listing.stdout.strip()
        if not top or STASH_TAG not in top:
            return None
        ref, _, subject = top.partition("\t")
        popped = subprocess.run(
            ["git", "stash", "pop", ref], cwd=os.getcwd(), capture_output=True, text=True, timeout=10
        )
        return subject if popped.returncode == 0 else None
    except Exception:
        return None


def _run_context_budget_triage(empirica_session, db_path=None):
    """Run context budget triage (evict low-priority before compaction).

    Returns budget report dict or None.
    """
    try:
        from empirica.core.context_budget import (
            ContextBudgetManager,
            load_thresholds_from_config,
        )

        thresholds = load_thresholds_from_config()
        manager = ContextBudgetManager(
            session_id=empirica_session,
            thresholds=thresholds,
            auto_subscribe=False,
        )
        manager._decay_all_items()
        report = manager.get_inventory_summary()
        manager.persist_state(db_path=db_path)
        return report
    except Exception:
        return None


def _capture_transaction_state(project_root, empirica_session=None, claude_session_id=None):
    """Capture active transaction state for continuity across compact.

    Returns (active_transaction, hook_counters) tuple.

    The transaction is located the way the Sentinel locates it: this hook's own
    suffix first, then a suffix-mismatched file matched by session. Looking only
    under this hook's suffix missed a transaction PREFLIGHT had opened from
    another terminal context (another X11 window, a tmux pane number that had
    rotated), so the snapshot carried no transaction and nothing was restored
    after compaction. The counters are read from beside the file found, never
    from this process's suffix, which could split the pair.
    """
    active_transaction = None
    hook_counters = None
    try:
        from project_resolver import _get_instance_suffix

        from empirica.utils.session_resolver import _find_transaction_file, hook_counters_path_for_transaction

        suffix = _get_instance_suffix()
        empirica_dir = project_root / ".empirica"

        tx_path = _find_transaction_file(empirica_dir, suffix, empirica_session, claude_session_id)
        if tx_path is None and not suffix:
            tx_files = list(empirica_dir.glob("active_transaction_*.json"))
            if tx_files:
                tx_path = max(tx_files, key=lambda p: p.stat().st_mtime)

        if tx_path is not None and tx_path.exists():
            with open(tx_path) as f:
                active_transaction = json.load(f)
            counters_path = hook_counters_path_for_transaction(tx_path)
            if counters_path.exists():
                with open(counters_path) as f:
                    hook_counters = json.load(f)
    except Exception:
        pass
    return active_transaction, hook_counters


def _assess_fresh_vectors(empirica_session):
    """Capture fresh epistemic vectors via assess-state.

    Returns (vectors_dict, error_string_or_None).
    """
    try:
        assess_result = subprocess.run(
            ["empirica", "assess-state", "--session-id", empirica_session, "--output", "json"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=os.getcwd(),
        )
        if assess_result.returncode == 0:
            assess_data = json.loads(assess_result.stdout)
            return assess_data.get("state", {}).get("vectors", {}), None
        return {}, assess_result.stderr
    except subprocess.TimeoutExpired:
        return {}, "assess-state timed out (>10s)"
    except Exception as e:
        return {}, str(e)


def _build_compact_guidance(breadcrumbs, active_transaction, display_vectors):
    """Build epistemic summary guidance for the summarizer.

    Returns (compact_guidance_str, has_open_transaction_bool).
    """
    session_short = (breadcrumbs.get("session_id") or "unknown")[:8]
    last_task = breadcrumbs.get("last_task", "")
    last_task_line = f"\nLast task: {last_task}" if last_task else ""

    has_open_transaction = active_transaction is not None and active_transaction.get("status") not in ("closed", None)

    if has_open_transaction:
        tx_id = active_transaction.get("transaction_id", "unknown")[:8]
        tx_status = active_transaction.get("status", "unknown")
        know_val = display_vectors.get("know", "N/A")
        unc_val = display_vectors.get("uncertainty", "N/A")
        vector_line = (
            f"\n3. OPEN TRANSACTION {tx_id} (status: {tx_status}) — vectors: know={know_val}, uncertainty={unc_val}"
        )
        vector_line += "\n   These vectors represent mid-transaction state. Resume from where you left off."
    else:
        vector_line = f"\n3. Session {session_short} COMPLETED (no open transaction). Previous vectors are historical — run fresh PREFLIGHT."

    compact_guidance = f"""Compaction summary guidance: Epistemic state has been captured externally (Empirica breadcrumbs, git notes) — compaction is routine and lossless for this session's work. The summarizer should prioritize:
1. What the user asked for and decisions made (not file contents)
2. Current task context and open questions (not code snippets){vector_line}{last_task_line}
File contents read during this session are available via Read tool — do NOT include them in the summary."""

    return compact_guidance, has_open_transaction


def _trace(outcome: str, **fields) -> None:
    """One JSON line per run to ~/.empirica/precompact.log, whatever the exit.

    Every early exit here is silent by design (a hook must not block
    compaction), which is how this project went 48 days without a snapshot
    while six sibling practices on the same box kept writing theirs: nothing
    recorded which exit fired. Post-compact consumes the handoff file, so no
    trace survived. This is the trace. Best-effort; never raises.
    """
    try:
        rec = {"ts": datetime.now().isoformat(timespec="seconds"), "cwd": os.getcwd(), "outcome": outcome}
        rec.update(fields)
        log = Path.home() / ".empirica" / "precompact.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:
        pass


def main():
    # Read hook input from stdin (provided by Claude Code)
    hook_input = json.loads(sys.stdin.read())

    trigger = hook_input.get("trigger", "auto")  # 'auto' or 'manual'
    claude_session_id = hook_input.get("session_id")

    # CRITICAL: Find and change to project root BEFORE importing empirica
    project_root = find_project_root(claude_session_id=claude_session_id)
    if project_root is None:
        _trace("no_project_root", trigger=trigger, claude_session_id=claude_session_id)
        print(json.dumps({}), file=sys.stdout)
        sys.exit(0)
    os.chdir(project_root)

    # STEP -1: restore a stash an OLDER version of this hook left behind (it used to stash the tree and pop it
    # afterwards; a kill between the two orphaned the stash). This hook no longer stashes anything. Kept for one
    # more release so a seat that still holds such a stash gets its tree back. Must happen BEFORE
    # _gather_git_context, or the breadcrumbs note records a clean tree that is only clean because the work is
    # still stashed.
    recovered = _recover_orphaned_stashes()

    # STEP 0: Extract task context + git state
    last_task = _extract_last_task(hook_input.get("transcript_path", ""))
    git_context = _gather_git_context()
    _write_compact_handoff(project_root, claude_session_id)

    # Auto-detect latest Empirica session
    empirica_session = _detect_empirica_session(claude_session_id)
    if not empirica_session:
        _trace("no_empirica_session", trigger=trigger, claude_session_id=claude_session_id, project_root=project_root)
        print(json.dumps({}), file=sys.stdout)
        sys.exit(0)

    # STEP 0.5-0.8: Pre-compaction housekeeping.
    # NOTE: goals are deliberately NOT touched here. Goals are the durable
    # layer that makes compaction lossless — flipping them stale on every
    # compact encoded a compaction-as-loss model into goal state (and fought
    # the migration-038 simplified lifecycle). Manual `goals-mark-stale`
    # remains available for genuine staleness triage.
    # This hook never touches the working tree. It used to stash uncommitted work "before snapshot" and pop it
    # afterwards; nothing consumed the stash, project-bootstrap ran after it and recorded a clean tree, it emptied
    # a shared checkout for up to ~25 s, and every way work was ever lost here (a skipped pop, someone else's
    # stash popped, SIGKILL) existed only because of it (David, 2026-10-13).
    return _main_guarded(
        recovered=recovered,
        trigger=trigger,
        empirica_session=empirica_session,
        last_task=last_task,
        git_context=git_context,
        project_root=project_root,
        claude_session_id=claude_session_id,
    )


def _main_guarded(
    *, recovered, trigger, empirica_session, last_task, git_context, project_root, claude_session_id=None
):
    practice_db = Path(project_root) / ".empirica" / "sessions" / "sessions.db"
    budget_report = _run_context_budget_triage(empirica_session, db_path=practice_db if practice_db.exists() else None)
    active_transaction, hook_counters = _capture_transaction_state(project_root, empirica_session)

    # STEP 1: Capture fresh epistemic vectors
    fresh_vectors, assess_error = _assess_fresh_vectors(empirica_session)

    # STEP 2: Run project-bootstrap for context anchor.
    # Precedence: explicit env override > canonical resolver (project.yaml →
    # basename) > legacy 'claude-code'. Bootstrapping the wrong practice
    # corrupts the post-compact context anchor, so resolve, don't guess.
    ai_id = os.getenv("EMPIRICA_AI_ID")
    if not ai_id:
        try:
            from empirica.utils.session_resolver import InstanceResolver as R

            ai_id = R.ai_id()
        except Exception:
            ai_id = None
    ai_id = ai_id or "claude-code"

    try:
        result = subprocess.run(
            [
                "empirica",
                "project-bootstrap",
                "--ai-id",
                ai_id,
                "--include-live-state",
                "--trigger",
                "pre_compact",
                "--output",
                "json",
            ],
            capture_output=True,
            text=True,
            # The harness gives this hook 30s TOTAL (settings.json). Handing all
            # 30 to one subprocess means the timeout that fires is the harness's
            # SIGKILL rather than our own catchable TimeoutExpired. Keep headroom.
            timeout=20,
            cwd=os.getcwd(),
        )

        if result.returncode != 0:
            _trace("bootstrap_failed", rc=result.returncode, stderr=result.stderr[:300], session=empirica_session)
            print(json.dumps({"stopReason": f"project-bootstrap failed: {result.stderr[:200]}"}), file=sys.stdout)
            sys.exit(2)

        bootstrap = json.loads(result.stdout) if result.stdout else {}
        breadcrumbs = bootstrap.get("breadcrumbs", bootstrap)

        # Save snapshot to .empirica/ref-docs
        ref_docs_dir = Path.cwd() / ".empirica" / "ref-docs"
        ref_docs_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        snapshot_path = ref_docs_dir / f"pre_summary_{timestamp}.json"

        live_state = breadcrumbs.get("live_state", {})
        fallback_vectors = live_state.get("vectors", {}) if live_state else {}

        snapshot = {
            "type": "pre_summary_snapshot",
            "timestamp": timestamp,
            **_snapshot_identity(claude_session_id, empirica_session),
            "session_id": breadcrumbs.get("session_id"),
            "trigger": trigger,
            "vectors_canonical": fresh_vectors,
            "vectors_source": "assess-state" if fresh_vectors else "live_state_fallback",
            "checkpoint": fresh_vectors or fallback_vectors,
            "live_state": live_state,
            "assess_error": assess_error,
            "breadcrumbs_summary": {
                "findings_count": len(breadcrumbs.get("findings", [])),
                "unknowns_count": len(breadcrumbs.get("unknowns", [])),
                "goals_count": len(breadcrumbs.get("goals", [])),
                "dead_ends_count": len(breadcrumbs.get("dead_ends", [])),
            },
            "context_budget": budget_report,
            "active_transaction": active_transaction,
            "hook_counters": hook_counters,
            "last_task": last_task,
            "git_context": git_context,
        }

        with open(snapshot_path, "w") as f:
            json.dump(snapshot, f, indent=2)
        _trace("snapshot_written", path=str(snapshot_path), trigger=trigger, session=empirica_session)

        # Write unified breadcrumbs git note
        session_id_for_notes = breadcrumbs.get("session_id") or empirica_session
        git_notes_written = _write_unified_breadcrumbs_note(
            session_id=session_id_for_notes,
            timestamp=timestamp,
            vectors=fresh_vectors or fallback_vectors,
            snapshot_filename=snapshot_path.name,
            breadcrumbs_summary=snapshot["breadcrumbs_summary"],
            last_task=last_task,
            git_context=git_context,
        )

        display_vectors = fresh_vectors or fallback_vectors
        vector_source = "canonical" if fresh_vectors else "fallback"
        compact_guidance, has_open_transaction = _build_compact_guidance(
            breadcrumbs, active_transaction, display_vectors
        )

        print(json.dumps({"systemMessage": compact_guidance}), file=sys.stdout)

        # Print user-visible message to stderr
        session_id_str = breadcrumbs.get("session_id", "Unknown")
        session_display = session_id_str[:8] if session_id_str else "Unknown"
        notes_msg = "✓" if git_notes_written else "✗"
        stash_msg = f"\n   Recovered an orphaned stash from an older run: {recovered}" if recovered else ""
        tx_state_msg = "OPEN (carrying through)" if has_open_transaction else "CLOSED (vectors historical)"
        print(
            f"""
📸 Empirica: Pre-compact snapshot saved — compaction is lossless (goals/artifacts/git-notes persist)
   Session: {session_display}...
   Trigger: {trigger}
   Transaction: {tx_state_msg}
   Vectors: {vector_source} ({len(display_vectors)} captured)
   Snapshot: {snapshot_path.name}
   Git notes: {notes_msg}{stash_msg}
""",
            file=sys.stderr,
        )

        sys.exit(0)

    except subprocess.TimeoutExpired:
        _trace("bootstrap_timeout", session=empirica_session)
        print(json.dumps({"stopReason": "project-bootstrap timed out (>30s)"}), file=sys.stdout)
        sys.exit(2)
    except Exception as e:
        _trace("error", error=f"{type(e).__name__}: {str(e)[:300]}", session=empirica_session)
        print(json.dumps({"stopReason": f"pre-compact error: {str(e)[:200]}"}), file=sys.stdout)
        sys.exit(2)


if __name__ == "__main__":
    main()

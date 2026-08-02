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
import signal
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


def _extract_last_task(transcript_path: str, max_chars: int = 500) -> str:
    """Extract the last human task message from the JSONL transcript.

    Filters out:
    - Tool results (content is array, not string)
    - Skill injections (<command-name> tags)
    - System reminders
    - Very long messages (>2000 chars = likely injected content)
    """
    if not transcript_path or not Path(transcript_path).exists():
        return ""

    try:
        lines = Path(transcript_path).read_text().strip().split("\n")
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
        subprocess.run(
            ["git", "notes", "--ref=breadcrumbs", "add", "-f", "-m", note, "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=os.getcwd(),
        )
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


def _detect_empirica_session():
    """Auto-detect latest Empirica session (no env var needed).

    Returns session ID string or None.
    """
    try:
        sys.path.insert(0, str(Path.home() / "empirical-ai" / "empirica"))
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


def _stash_uncommitted_work(empirica_session):
    """Stash uncommitted work before snapshot.

    Returns the created stash's commit sha, or None if nothing was stashed.

    The sha — not a bool — is the return value because the restore must pop
    the stash THIS hook created. `git stash pop` with no argument takes the
    top of the stack, and in a shared checkout (several instances, one working
    tree) another instance's stash can land on top between push and pop. Popping
    by position would then restore someone else's work over this tree and leave
    ours buried.
    """
    try:
        status_result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=os.getcwd(), capture_output=True, text=True, timeout=5
        )
        if not status_result.stdout.strip():
            return None
        stash_msg = f"{STASH_TAG} {empirica_session[:8]} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        result = subprocess.run(
            ["git", "stash", "push", "-m", stash_msg, "--include-untracked"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return None
        sha = subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "refs/stash"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return sha.stdout.strip() or None
    except Exception:
        pass
    return None


def _find_stash_ref(sha):
    """Return the stash@{n} that currently holds `sha`, or None.

    Positions shift as other stashes are pushed or dropped, so the ref is
    resolved at restore time rather than assumed to still be stash@{0}.
    """
    try:
        listing = subprocess.run(
            ["git", "stash", "list", "--format=%H"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5,
        )
        for index, line in enumerate(listing.stdout.splitlines()):
            if line.strip() == sha:
                return f"stash@{{{index}}}"
    except Exception:
        pass
    return None


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


def _run_context_budget_triage(empirica_session):
    """Run context budget triage (evict low-priority before compaction).

    Returns budget report dict or None.
    """
    try:
        sys.path.insert(0, str(Path.home() / "empirical-ai" / "empirica"))
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
        manager.persist_state()
        return report
    except Exception:
        return None


def _capture_transaction_state(project_root):
    """Capture active transaction state for continuity across compact.

    Returns (active_transaction, hook_counters) tuple.
    """
    active_transaction = None
    hook_counters = None
    try:
        from project_resolver import _get_instance_suffix

        suffix = _get_instance_suffix()

        if suffix:
            tx_path = project_root / ".empirica" / f"active_transaction{suffix}.json"
            if tx_path.exists():
                with open(tx_path) as f:
                    active_transaction = json.load(f)
            counters_path = project_root / ".empirica" / f"hook_counters{suffix}.json"
            if counters_path.exists():
                with open(counters_path) as f:
                    hook_counters = json.load(f)
        else:
            tx_files = list((project_root / ".empirica").glob("active_transaction_*.json"))
            if tx_files:
                tx_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                with open(tx_files[0]) as f:
                    active_transaction = json.load(f)
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


def _restore_stash(stash_sha):
    """Pop the stash identified by `stash_sha`. Returns True on success.

    Idempotent: a sha no longer on the stack has already been restored, which
    is success, not failure — so calling this twice (finally-block after a
    happy-path call, say) is harmless.
    """
    if not stash_sha:
        return False
    try:
        ref = _find_stash_ref(stash_sha)
        if ref is None:
            return True
        pop_result = subprocess.run(
            ["git", "stash", "pop", ref], cwd=os.getcwd(), capture_output=True, text=True, timeout=10
        )
        return pop_result.returncode == 0
    except Exception:
        return False


def main():
    # Read hook input from stdin (provided by Claude Code)
    hook_input = json.loads(sys.stdin.read())

    trigger = hook_input.get("trigger", "auto")  # 'auto' or 'manual'
    claude_session_id = hook_input.get("session_id")

    # CRITICAL: Find and change to project root BEFORE importing empirica
    project_root = find_project_root(claude_session_id=claude_session_id)
    if project_root is None:
        print(json.dumps({}), file=sys.stdout)
        sys.exit(0)
    os.chdir(project_root)

    # STEP -1: restore anything a previous run stashed and was killed before
    # popping. Must happen BEFORE _gather_git_context, or the breadcrumbs note
    # records a clean tree that is only clean because the work is still stashed.
    recovered = _recover_orphaned_stashes()

    # STEP 0: Extract task context + git state
    last_task = _extract_last_task(hook_input.get("transcript_path", ""))
    git_context = _gather_git_context()
    _write_compact_handoff(project_root, claude_session_id)

    # Auto-detect latest Empirica session
    empirica_session = _detect_empirica_session()
    if not empirica_session:
        print(json.dumps({}), file=sys.stdout)
        sys.exit(0)

    # STEP 0.5-0.8: Pre-compaction housekeeping.
    # NOTE: goals are deliberately NOT touched here. Goals are the durable
    # layer that makes compaction lossless — flipping them stale on every
    # compact encoded a compaction-as-loss model into goal state (and fought
    # the migration-038 simplified lifecycle). Manual `goals-mark-stale`
    # remains available for genuine staleness triage.
    # The stash and its pop are a single coupled lifecycle: from here to the
    # `finally` the user's working tree exists ONLY inside that stash, so every
    # way out of this function has to go through the restore. Before this was
    # made total, three `sys.exit(2)` paths and the harness timeout each left
    # the tree empty and the stash orphaned — silently, and while reporting
    # "stash: saved+restored".
    stash_sha = _stash_uncommitted_work(empirica_session)
    _install_stash_guard(stash_sha)
    try:
        return _main_guarded(
            stash_sha=stash_sha,
            recovered=recovered,
            trigger=trigger,
            empirica_session=empirica_session,
            last_task=last_task,
            git_context=git_context,
            project_root=project_root,
        )
    finally:
        # Idempotent: a no-op when the happy path already popped it.
        _restore_stash(stash_sha)


def _install_stash_guard(stash_sha):
    """Pop the stash on SIGTERM — the harness's hook-timeout signal.

    SIGKILL cannot be caught; `_recover_orphaned_stashes` on the next run is
    what covers that case.
    """
    if not stash_sha:
        return

    def _handler(signum, frame):
        _restore_stash(stash_sha)
        sys.exit(143)

    try:
        signal.signal(signal.SIGTERM, _handler)
    except (ValueError, OSError):
        pass  # not the main thread, or no signal support — finally still covers exits


def _main_guarded(*, stash_sha, recovered, trigger, empirica_session, last_task, git_context, project_root):
    budget_report = _run_context_budget_triage(empirica_session)
    active_transaction, hook_counters = _capture_transaction_state(project_root)

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
            # 30 to one subprocess leaves nothing to pop the stash with, so the
            # timeout that fires is the harness's SIGKILL rather than our own
            # catchable TimeoutExpired. Keep headroom.
            timeout=20,
            cwd=os.getcwd(),
        )

        if result.returncode != 0:
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

        restored = _restore_stash(stash_sha)

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
        # Keyed on the POP, not on the push. Reporting "restored" because a
        # stash was created is the unfalsifiable-success shape: it reads
        # identically whether the tree came back or is still sitting in the
        # stash. When it did not come back, print the recovery command — the
        # user cannot act on a status they were not given.
        if not stash_sha:
            stash_msg = ""
        elif restored:
            stash_msg = " (stash: saved+restored)"
        else:
            stash_msg = " (stash: ORPHANED — your tree is in `git stash list`; recover with `git stash pop`)"
        if recovered:
            stash_msg += f"\n   Recovered an orphaned stash from a previous run: {recovered}"
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
        print(json.dumps({"stopReason": "project-bootstrap timed out (>30s)"}), file=sys.stdout)
        sys.exit(2)
    except Exception as e:
        print(json.dumps({"stopReason": f"pre-compact error: {str(e)[:200]}"}), file=sys.stdout)
        sys.exit(2)


if __name__ == "__main__":
    main()

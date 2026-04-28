"""
Post-Test Evidence Collector

Gathers objective, non-self-referential evidence from available sources:

Universal (all phases):
- Artifact metrics: findings, unknowns, dead-ends, mistakes (SQLite)
- Noetic quality: investigation depth, breadcrumb density (SQLite)
- Sentinel gate decisions: CHECK proceed/investigate ratio (SQLite)

Praxic phase (after CHECK):
- Goal/subtask completion metrics (SQLite)
- Issue and triage metrics (SQLite)
- Codebase model: entities discovered, facts created, constraints (SQLite)
- Non-git file changes: files edited outside git repos (transaction file + mtime)
- Git metrics: commits, file change density (subprocess, optional)
- Code quality: ruff, radon, pyright on changed files (subprocess, optional)
- Test results from pytest JSON report (file-based, optional)

Each evidence source is independent and failure-tolerant. The collector
returns whatever evidence it can gather.

All evidence gathered here consists of deterministic proxies — observable signals
that correlate with epistemic state but cannot fully measure it. These are useful
for detecting systematic calibration drift over many transactions, not for judging
any single transaction's epistemic accuracy. See grounded_calibration.py module
docstring for the full dual-track calibration philosophy.
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, ClassVar

logger = logging.getLogger(__name__)


class EvidenceQuality(Enum):
    """How reliable is this evidence source?"""
    OBJECTIVE = "objective"
    SEMI_OBJECTIVE = "semi_objective"
    INFERRED = "inferred"


@dataclass
class EvidenceItem:
    """A single piece of objective evidence."""
    source: str
    metric_name: str
    value: float
    raw_value: Any
    quality: EvidenceQuality
    supports_vectors: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvidenceBundle:
    """Complete evidence collection for a session.

    Three source-state lists distinguish between collector outcomes:
    - sources_available: ran successfully and returned 1+ items (real measurement)
    - sources_empty: ran successfully but returned 0 items (no signal to grade)
    - sources_failed: raised an exception (real failure, see source_errors)

    source_errors maps source name → "ExceptionType: truncated message" for
    every failed collector. Captured at DEBUG log level by default; surfaced
    in POSTFLIGHT response when sources_failed is non-empty so failures
    can be debugged from the output alone.
    """
    session_id: str
    items: list[EvidenceItem] = field(default_factory=list)
    collection_timestamp: float = 0.0
    sources_available: list[str] = field(default_factory=list)
    sources_failed: list[str] = field(default_factory=list)
    sources_empty: list[str] = field(default_factory=list)
    source_errors: dict[str, str] = field(default_factory=dict)
    coverage: float = 0.0



# Weight applied to artifacts not linked to any session goal.
# Unscoped unknowns (future research, general observations) still contribute
# but with reduced influence so they don't artificially depress KNOW grounding.
UNSCOPED_ARTIFACT_WEIGHT = 0.3


class EvidenceProfile:
    """Evidence collection profile — determines which collectors run.

    Profiles:
    - "code": ruff, radon, pyright, pytest, git (default for repos with .py files)
    - "prose": textstat, proselint, vale, document metrics, source quality
    - "web": build verification, HTML validation, link integrity, terminology, assets
    - "hybrid": all evidence sources (code + prose + web when detected)
    - "insufficient": no measurable signal — only universal collectors run.
      Used when there are no changed files at all (out-of-repo work, remote-ops
      without explicit declaration). The AI's self-assessment is the authority
      because no profile-specific source has anything meaningful to grade.
    - "auto": detect from project content (falls back to "insufficient" if no signal)

    Set via:
    - project.yaml: evidence_profile: web
    - CLI flag: --evidence-profile web
    - Environment: EMPIRICA_EVIDENCE_PROFILE=web
    """
    CODE = "code"
    PROSE = "prose"
    WEB = "web"
    HYBRID = "hybrid"
    INSUFFICIENT = "insufficient"
    AUTO = "auto"

    VALID: ClassVar[set[str]] = {CODE, PROSE, WEB, HYBRID, INSUFFICIENT, AUTO}

    @staticmethod
    def resolve(explicit: str | None = None,
                project_path: str | None = None) -> str:
        """Resolve the evidence profile from explicit flag, config, or env."""
        import os

        # 1. Explicit flag takes priority
        if explicit and explicit in EvidenceProfile.VALID:
            return explicit

        # 2. Environment variable
        env_profile = os.environ.get("EMPIRICA_EVIDENCE_PROFILE", "").lower()
        if env_profile in EvidenceProfile.VALID:
            return env_profile

        # 3. Project config
        if project_path:
            try:
                import yaml
                config_path = Path(project_path) / ".empirica" / "project.yaml"
                if config_path.exists():
                    with open(config_path) as f:
                        config = yaml.safe_load(f) or {}
                    profile = config.get("evidence_profile", "").lower()
                    if profile in EvidenceProfile.VALID:
                        return profile
            except Exception:
                pass

        # 4. Auto-detect
        return EvidenceProfile.AUTO


class PostTestCollector:
    """Collects objective evidence from multiple sources."""

    def __init__(self, session_id: str, project_id: str | None = None,
                 db=None, phase: str = "combined",
                 check_timestamp: float | None = None,
                 evidence_profile: str | None = None,
                 work_context: str | None = None,
                 preflight_timestamp: float | None = None,
                 transaction_id: str | None = None):
        self.session_id = session_id
        self.project_id = project_id
        self.phase = phase  # "noetic", "praxic", or "combined"
        self.check_timestamp = check_timestamp  # CHECK boundary timestamp
        self.preflight_timestamp = preflight_timestamp  # Transaction start
        self.transaction_id = transaction_id  # Scopes evidence to this transaction
        self.evidence_profile = evidence_profile
        self.work_context = work_context  # greenfield|iteration|investigation|refactor
        self._db = db
        self._owns_db = False
        self._session_goal_ids: list[str] | None = None
        self._project_root: str | None = None  # Lazy-resolved
        self._project_maturity: dict[str, Any] | None = None  # Lazy-resolved

    def _get_db(self):
        if self._db is None:
            from empirica.data.session_database import SessionDatabase
            self._db = SessionDatabase()
            self._owns_db = True
        return self._db

    def _close_db(self):
        if self._owns_db and self._db is not None:
            self._db.close()
            self._db = None
            self._owns_db = False

    def _tx_filter(self, table_alias: str = "") -> tuple:
        """Build transaction-scoped WHERE clause and params.

        Priority: transaction_id > preflight_timestamp > session_id only.
        Returns (sql_fragment, params_tuple) to append to a WHERE clause.
        The sql_fragment always starts with "AND".
        """
        prefix = f"{table_alias}." if table_alias else ""
        if self.transaction_id:
            return (f"AND {prefix}transaction_id = ?", (self.transaction_id,))
        if self.preflight_timestamp:
            return (f"AND {prefix}created_timestamp >= ?", (self.preflight_timestamp,))
        return ("", ())

    def _get_session_goal_ids(self) -> list[str]:
        """Get goal IDs for this session (cached)."""
        if self._session_goal_ids is not None:
            return self._session_goal_ids
        db = self._get_db()
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT id FROM goals WHERE session_id = ?",
            (self.session_id,),
        )
        self._session_goal_ids = [row[0] for row in cursor.fetchall()]
        return self._session_goal_ids

    def _resolve_profile(self) -> str:
        """Resolve evidence profile, auto-detecting if needed."""
        profile = EvidenceProfile.resolve(
            explicit=self.evidence_profile,
            project_path=self._resolve_project_root(),
        )
        if profile == EvidenceProfile.AUTO:
            # Auto-detect from changed file extensions
            changed = self._get_session_changed_files()
            has_code = any(f.endswith('.py') for f in changed)
            from .web_collector import WEB_EXTENSIONS
            has_web = any(Path(f).suffix in WEB_EXTENSIONS for f in changed)
            if has_web and has_code:
                return EvidenceProfile.HYBRID
            elif has_web:
                return EvidenceProfile.WEB
            elif has_code:
                return EvidenceProfile.CODE
            elif changed:
                # Non-empty but neither code nor web → markdown, configs, data
                return EvidenceProfile.PROSE
            else:
                # Empty changed files → measurer has no file signal at all.
                # Out-of-repo work, remote-ops without explicit declaration, or
                # nothing-yet. Halt profile-specific collection; rely only on
                # universal collectors (artifacts, goals, etc.) and let the
                # downstream coverage threshold gate decide whether calibration
                # is meaningful at all.
                return EvidenceProfile.INSUFFICIENT
        return profile

    def _resolve_project_root(self) -> str | None:
        """Resolve the project root path for subprocess cwd and file lookups.

        Priority chain:
        1. project_id → workspace.db → trajectory_path (authoritative)
        2. get_active_project_path() (instance/active_work files)
        3. CWD-based git rev-parse (last resort fallback)

        Result is cached on self._project_root after first call.
        """
        if self._project_root is not None:
            return self._project_root

        # Priority 1: Resolve from project_id via workspace.db
        if self.project_id:
            try:
                from empirica.utils.session_resolver import InstanceResolver as R
                project_info = R.resolve_workspace_project(self.project_id)
                if project_info and (project_info.get("project_path") or project_info.get("trajectory_path")):
                    path = project_info.get("project_path") or project_info["trajectory_path"]
                    if Path(path).is_dir():
                        self._project_root = path
                        return self._project_root
            except Exception:
                pass

        # Priority 2: Active project path from instance/active_work files
        try:
            from empirica.utils.session_resolver import InstanceResolver as R
            active_path = R.project_path()
            if active_path and Path(active_path).is_dir():
                self._project_root = active_path
                return self._project_root
        except Exception:
            pass

        # Priority 3: CWD-based git root (last resort)
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                self._project_root = result.stdout.strip()
                return self._project_root
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

        return None

    def _detect_project_maturity(self) -> dict[str, Any]:
        """Detect project maturity from git history for normalization curve selection.

        Returns cached dict with:
        - total_commits: int — total commits in repo history
        - is_greenfield: bool — ≤3 prior commits (new project)
        - is_young: bool — <10 commits
        - maturity: str — "greenfield" | "young" | "mature"

        Used by _collect_git_metrics and _collect_issue_metrics to adjust
        normalization divisors. A root commit creating 8 files is maximal change,
        not 0.2 change.
        """
        if self._project_maturity is not None:
            return self._project_maturity

        # Default: assume mature (current behavior)
        result = {"total_commits": 100, "is_greenfield": False,
                  "is_young": False, "maturity": "mature"}

        # work_context override: if explicitly set to greenfield, trust it
        if self.work_context == "greenfield":
            result.update({"total_commits": 1, "is_greenfield": True,
                           "is_young": True, "maturity": "greenfield"})
            self._project_maturity = result
            return result

        project_root = self._resolve_project_root()
        if not project_root:
            self._project_maturity = result
            return result

        try:
            proc = subprocess.run(
                ["git", "rev-list", "--count", "HEAD"],
                capture_output=True, text=True, timeout=5,
                cwd=project_root,
            )
            if proc.returncode == 0:
                total = int(proc.stdout.strip())
                result["total_commits"] = total
                result["is_greenfield"] = total <= 3
                result["is_young"] = total < 10
                if total <= 3:
                    result["maturity"] = "greenfield"
                elif total < 10:
                    result["maturity"] = "young"
                else:
                    result["maturity"] = "mature"
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass

        self._project_maturity = result
        return result

    def _get_prose_collectors(self) -> list[tuple]:
        """Get prose-specific evidence collectors."""
        from .prose_collector import ProseEvidenceCollector
        prose = ProseEvidenceCollector(
            session_id=self.session_id,
            project_id=self.project_id,
            db=self._get_db(),
            phase=self.phase,
            check_timestamp=self.check_timestamp,
        )
        return [
            ("prose_quality", prose._collect_prose_quality),
            ("document_metrics", prose._collect_document_metrics),
            ("source_quality", prose._collect_source_quality),
            ("action_verification", prose._collect_action_verification),
        ]

    def _get_web_collectors(self) -> list[tuple]:
        """Get web-specific evidence collectors."""
        from .web_collector import WebEvidenceCollector
        web = WebEvidenceCollector(
            session_id=self.session_id,
            project_id=self.project_id,
            db=self._get_db(),
            phase=self.phase,
            check_timestamp=self.check_timestamp,
        )
        return [("web", lambda: web.collect_all())]

    def collect_all(self) -> EvidenceBundle:
        """Collect evidence from all available sources.

        Phase-aware and profile-aware collection:
        - Phase: "noetic" / "praxic" / "combined"
        - Profile: "code" / "prose" / "hybrid" / "auto"
        """
        bundle = EvidenceBundle(
            session_id=self.session_id,
            collection_timestamp=time.time(),
        )

        profile = self._resolve_profile()
        logger.debug(f"Evidence profile: {profile} (phase: {self.phase})")

        # Universal collectors (always run regardless of profile)
        universal = [
            ("artifacts", self._collect_artifact_metrics),
        ]

        # Phase-specific universal collectors
        if self.phase in ("noetic", "combined"):
            universal.append(("noetic", self._collect_noetic_metrics))
            universal.append(("sentinel", self._collect_sentinel_metrics))
        if self.phase in ("praxic", "combined"):
            universal.append(("goals", self._collect_goal_metrics))
            universal.append(("issues", self._collect_issue_metrics))
            universal.append(("triage", self._collect_triage_metrics))
            universal.append(("codebase_model", self._collect_codebase_model_metrics))
            universal.append(("non_git_files", self._collect_non_git_file_metrics))

        # Profile-specific collectors — only run during praxic/combined phases
        # AND when the profile actually has signal to grade. These measure
        # OUTPUT quality (code quality, test results, build verification,
        # document metrics) which is meaningless during:
        #   - pure noetic investigation (no praxic output yet)
        #   - INSUFFICIENT profile (no changed files at all — out-of-repo work)
        # Noetic grounding relies on epistemic process evidence (artifacts,
        # thoroughness, sentinel decisions) from the universal collectors above.
        # This applies across ALL domains — not just software engineering.
        if self.phase == "noetic" or profile == EvidenceProfile.INSUFFICIENT:
            profile_collectors = []
        elif profile == EvidenceProfile.CODE:
            profile_collectors = [
                ("pytest", self._collect_test_results),
                ("git", self._collect_git_metrics),
                ("code_quality", self._collect_code_quality_metrics),
            ]
        elif profile == EvidenceProfile.PROSE:
            profile_collectors = self._get_prose_collectors()
        elif profile == EvidenceProfile.WEB:
            profile_collectors = self._get_web_collectors()
        elif profile == EvidenceProfile.HYBRID:
            profile_collectors = [
                ("pytest", self._collect_test_results),
                ("git", self._collect_git_metrics),
                ("code_quality", self._collect_code_quality_metrics),
            ] + self._get_prose_collectors() + self._get_web_collectors()
        else:
            # Fallback to code
            profile_collectors = [
                ("pytest", self._collect_test_results),
                ("git", self._collect_git_metrics),
                ("code_quality", self._collect_code_quality_metrics),
            ]

        collectors = universal + profile_collectors

        for source_name, collector_fn in collectors:
            try:
                items = collector_fn()
                if items:
                    bundle.items.extend(items)
                    bundle.sources_available.append(source_name)
                else:
                    # Ran cleanly but found nothing to grade. This is
                    # different from crashing — track it separately so
                    # genuine failures can be debugged.
                    bundle.sources_empty.append(source_name)
            except Exception as e:
                logger.debug(f"Evidence source {source_name} failed: {e}")
                bundle.sources_failed.append(source_name)
                # Capture exception type + truncated message so POSTFLIGHT
                # output can surface the actual cause. Without this,
                # sources_failed is opaque ("it failed but why?").
                err_type = type(e).__name__
                err_msg = str(e)[:200]
                bundle.source_errors[source_name] = f"{err_type}: {err_msg}"

        grounded_vectors = set()
        for item in bundle.items:
            grounded_vectors.update(item.supports_vectors)
        bundle.coverage = len(grounded_vectors) / 13.0

        self._close_db()
        return bundle

    def _collect_noetic_metrics(self) -> list[EvidenceItem]:
        """Collect investigation-phase evidence for noetic calibration.

        Noetic evidence measures epistemic process quality:
        - Investigation coverage (files examined, queries issued)
        - Unknowns surfaced during investigation
        - Dead-ends identified before hitting them
        - CHECK gate iterations (investigate rounds)
        """
        items = []
        db = self._get_db()
        cursor = db.conn.cursor()

        # Investigation depth: unknowns surfaced (more = better epistemic honesty)
        tx_sql, tx_params = self._tx_filter()
        cursor.execute(f"""
            SELECT COUNT(*) FROM project_unknowns
            WHERE session_id = ? {tx_sql}
        """, (self.session_id, *tx_params))
        unknowns_surfaced = cursor.fetchone()[0]

        if unknowns_surfaced > 0:
            # Normalize: 1-2 = 0.3, 5+ = 1.0
            #
            # Semantic: surfacing unknowns is a KNOW signal (honesty about the
            # boundary of your knowledge), not directly an uncertainty signal.
            # A transaction that surfaced 5 unknowns demonstrates high knowledge
            # honesty — it doesn't tell us whether the AI is "uncertain" overall.
            #
            # Fixed 2026-04-07: prior version assigned this score to BOTH know
            # and uncertainty, which double-counted the same evidence with
            # different (conflicting) semantics. Unknowns_surfaced now grounds
            # know only. Uncertainty is grounded by dead_end_ratio (artifacts
            # source) and investigation_rounds (sentinel source) which both
            # measure actual doubt rather than disclosure honesty.
            honesty_score = min(1.0, unknowns_surfaced / 5.0)
            items.append(EvidenceItem(
                source="noetic",
                metric_name="unknowns_surfaced",
                value=honesty_score,
                raw_value={"count": unknowns_surfaced},
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["know"],
                metadata={"phase": "noetic"},
            ))

        # Dead-end avoidance: dead-ends logged before CHECK proceed
        if self.check_timestamp:
            cursor.execute(f"""
                SELECT COUNT(*) FROM project_dead_ends
                WHERE session_id = ? AND created_timestamp <= ? {tx_sql}
            """, (self.session_id, self.check_timestamp, *tx_params))
            pre_check_dead_ends = cursor.fetchone()[0]

            if pre_check_dead_ends > 0:
                # Identifying dead-ends before action = good pattern recognition
                avoidance_score = min(1.0, pre_check_dead_ends / 3.0)
                items.append(EvidenceItem(
                    source="noetic",
                    metric_name="dead_end_avoidance",
                    value=avoidance_score,
                    raw_value={"pre_check_dead_ends": pre_check_dead_ends},
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["signal", "know"],
                    metadata={"phase": "noetic"},
                ))

        # Findings logged during investigation (pre-CHECK)
        if self.check_timestamp:
            cursor.execute(f"""
                SELECT COUNT(*) FROM project_findings
                WHERE session_id = ? AND created_timestamp <= ? {tx_sql}
            """, (self.session_id, self.check_timestamp, *tx_params))
            pre_check_findings = cursor.fetchone()[0]
        else:
            cursor.execute(f"""
                SELECT COUNT(*) FROM project_findings
                WHERE session_id = ? {tx_sql}
            """, (self.session_id, *tx_params))
            pre_check_findings = cursor.fetchone()[0]

        if pre_check_findings > 0:
            # More findings during investigation = richer epistemic output
            discovery_score = min(1.0, pre_check_findings / 5.0)
            items.append(EvidenceItem(
                source="noetic",
                metric_name="investigation_findings",
                value=discovery_score,
                raw_value={"findings": pre_check_findings},
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["know", "signal"],
                metadata={"phase": "noetic"},
            ))

        # CHECK iteration count: more investigate rounds = thorough but uncertain
        tx_ref_sql, tx_ref_params = self._tx_filter()
        cursor.execute(f"""
            SELECT reflex_data FROM reflexes
            WHERE session_id = ? AND phase = 'CHECK' {tx_ref_sql}
            ORDER BY timestamp ASC
        """, (self.session_id, *tx_ref_params))
        check_rows = cursor.fetchall()

        investigate_count = 0
        for row in check_rows:
            try:
                data = json.loads(row[0]) if row[0] else {}
                if data.get("decision") == "investigate":
                    investigate_count += 1
            except (json.JSONDecodeError, TypeError):
                pass

        if len(check_rows) > 0:
            # Investigation thoroughness: at least 1 investigate round = thorough
            # But too many rounds (5+) suggests struggling, not thoroughness
            if investigate_count == 0:
                thoroughness = 0.5  # Went straight to proceed — moderate
            elif investigate_count <= 3:
                thoroughness = 0.7 + (investigate_count * 0.1)  # 0.8-1.0
            else:
                thoroughness = max(0.4, 1.0 - (investigate_count - 3) * 0.15)

            items.append(EvidenceItem(
                source="noetic",
                metric_name="investigation_thoroughness",
                value=thoroughness,
                raw_value={
                    "investigate_rounds": investigate_count,
                    "total_checks": len(check_rows),
                },
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["know", "context"],
                metadata={"phase": "noetic"},
            ))

        return items

    def _collect_goal_metrics(self) -> list[EvidenceItem]:
        """Collect goal/subtask completion ratios from SQLite."""
        items = []
        db = self._get_db()
        cursor = db.conn.cursor()

        # Subtask completion ratio — scoped to transaction's goals if available
        if self.transaction_id:
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM subtasks s
                JOIN goals g ON s.goal_id = g.id
                WHERE g.transaction_id = ?
            """, (self.transaction_id,))
        else:
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN s.status = 'completed' THEN 1 ELSE 0 END) as completed
                FROM subtasks s
                JOIN goals g ON s.goal_id = g.id
                WHERE g.session_id = ?
            """, (self.session_id,))
        row = cursor.fetchone()

        if row and row[0] > 0:
            total, completed = row[0], row[1]
            ratio = completed / total
            items.append(EvidenceItem(
                source="goals",
                metric_name="subtask_completion_ratio",
                value=ratio,
                raw_value={"completed": completed, "total": total},
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["completion", "do"],
                metadata={"session_id": self.session_id},
            ))

            # Goal completion also grounds impact and change:
            # completing goals = delivering impact, creating change
            if completed > 0:
                # Impact: completing any goals shows delivered value
                impact_score = min(1.0, ratio * 1.2)  # Boost slightly — completion is strong impact signal
                items.append(EvidenceItem(
                    source="goals",
                    metric_name="goal_completion_impact",
                    value=impact_score,
                    raw_value={"completed": completed, "total": total},
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["impact"],
                    metadata={"session_id": self.session_id},
                ))

                # Change: completed goals = state change happened
                change_score = ratio  # Direct ratio — completing half the goals = 0.5 change
                items.append(EvidenceItem(
                    source="goals",
                    metric_name="goal_completion_change",
                    value=change_score,
                    raw_value={"completed": completed, "total": total},
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["change"],
                    metadata={"session_id": self.session_id},
                ))

        # Token estimation accuracy — scoped to transaction's goals if available
        goal_scope_col = "g.transaction_id" if self.transaction_id else "g.session_id"
        goal_scope_val = self.transaction_id or self.session_id
        cursor.execute(f"""
            SELECT
                SUM(s.estimated_tokens) as est,
                SUM(s.actual_tokens) as actual
            FROM subtasks s
            JOIN goals g ON s.goal_id = g.id
            WHERE {goal_scope_col} = ?
              AND s.estimated_tokens IS NOT NULL
              AND s.actual_tokens IS NOT NULL
              AND s.estimated_tokens > 0
        """, (goal_scope_val,))
        row = cursor.fetchone()

        if row and row[0] and row[1] and row[0] > 0:
            est, actual = row[0], row[1]
            # Accuracy = 1.0 - abs(error_ratio), clamped to [0, 1]
            error_ratio = abs(actual - est) / est
            accuracy = max(0.0, 1.0 - error_ratio)
            items.append(EvidenceItem(
                source="goals",
                metric_name="token_estimation_accuracy",
                value=accuracy,
                raw_value={"estimated": est, "actual": actual},
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["know", "clarity"],
                metadata={"error_ratio": error_ratio},
            ))

        return items

    def _query_scope_weighted_unknowns(self, cursor, goal_ids, has_goals, tx_sql, tx_params, tx_sql_u, tx_params_u):
        """Query scope-weighted unknown counts. Returns (weighted_total, weighted_resolved, scoped_total, unscoped_total)."""
        if has_goals:
            placeholders = ",".join("?" for _ in goal_ids)
            cursor.execute(f"""
                SELECT COUNT(*) as total, SUM(CASE WHEN u.is_resolved = 1 THEN 1 ELSE 0 END) as resolved
                FROM project_unknowns u LEFT JOIN goals g ON u.goal_id = g.id
                WHERE u.session_id = ? AND u.goal_id IN ({placeholders})
                  AND NOT (u.is_resolved = 0 AND g.status = 'completed') {tx_sql_u}
            """, (self.session_id, *goal_ids, *tx_params_u))
            row = cursor.fetchone()
            scoped_total = row[0] if row else 0
            scoped_resolved = row[1] or 0 if row else 0
            cursor.execute(f"""
                SELECT COUNT(*) as total, SUM(CASE WHEN is_resolved = 1 THEN 1 ELSE 0 END) as resolved
                FROM project_unknowns WHERE session_id = ?
                  AND (goal_id IS NULL OR goal_id = '' OR goal_id NOT IN ({placeholders})) {tx_sql}
            """, (self.session_id, *goal_ids, *tx_params))
            row = cursor.fetchone()
            unscoped_total = row[0] if row else 0
            unscoped_resolved = row[1] or 0 if row else 0
            w = UNSCOPED_ARTIFACT_WEIGHT
            return scoped_total + (unscoped_total * w), scoped_resolved + (unscoped_resolved * w), scoped_total, unscoped_total
        else:
            cursor.execute(f"""
                SELECT COUNT(*) as total, SUM(CASE WHEN is_resolved = 1 THEN 1 ELSE 0 END) as resolved
                FROM project_unknowns WHERE session_id = ? {tx_sql}
            """, (self.session_id, *tx_params))
            row = cursor.fetchone()
            wt = row[0] if row else 0
            wr = row[1] or 0 if row else 0
            return wt, wr, 0, wt

    def _query_scope_weighted_count(self, cursor, table, goal_ids, has_goals, tx_sql, tx_params):
        """Query scope-weighted count for a table. Returns (weighted_count, scoped, unscoped)."""
        if has_goals:
            placeholders = ",".join("?" for _ in goal_ids)
            cursor.execute(f"""
                SELECT SUM(CASE WHEN goal_id IN ({placeholders}) THEN 1 ELSE 0 END),
                       SUM(CASE WHEN goal_id IS NULL OR goal_id = '' OR goal_id NOT IN ({placeholders}) THEN 1 ELSE 0 END)
                FROM {table} WHERE session_id = ? {tx_sql}
            """, (*goal_ids, *goal_ids, self.session_id, *tx_params))
            row = cursor.fetchone()
            scoped = row[0] or 0 if row else 0
            unscoped = row[1] or 0 if row else 0
            return scoped + (unscoped * UNSCOPED_ARTIFACT_WEIGHT), scoped, unscoped
        else:
            cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE session_id = ? {tx_sql}", (self.session_id, *tx_params))
            total = cursor.fetchone()[0]
            return total, 0, total

    def _collect_artifact_metrics(self) -> list[EvidenceItem]:
        """Collect scope-weighted noetic artifact counts for this transaction.

        Artifacts linked to session goals count at full weight.
        Unscoped artifacts (no goal_id — typically future research or general
        observations) count at UNSCOPED_ARTIFACT_WEIGHT to avoid artificially
        depressing KNOW grounding when forward-looking unknowns are captured.

        Transaction-scoped: filters by transaction_id when available,
        falls back to preflight_timestamp, then session_id only.
        """
        items = []
        db = self._get_db()
        cursor = db.conn.cursor()
        goal_ids = self._get_session_goal_ids()
        has_goals = len(goal_ids) > 0
        tx_sql, tx_params = self._tx_filter()
        tx_sql_u, tx_params_u = self._tx_filter("u")

        # --- Scope-weighted unknowns ---
        unknowns_weighted_total, unknowns_weighted_resolved, scoped_total, unscoped_total = \
            self._query_scope_weighted_unknowns(cursor, goal_ids, has_goals, tx_sql, tx_params, tx_sql_u, tx_params_u)

        # --- Scope-weighted findings ---
        findings_count, scoped_findings, unscoped_findings = \
            self._query_scope_weighted_count(cursor, "project_findings", goal_ids, has_goals, tx_sql, tx_params)

        # --- Scope-weighted dead-ends ---
        dead_ends_count, _scoped_de, _unscoped_de = \
            self._query_scope_weighted_count(cursor, "project_dead_ends", goal_ids, has_goals, tx_sql, tx_params)

        # Mistakes count (not scope-weighted — all mistakes are relevant)
        cursor.execute(f"""
            SELECT COUNT(*) FROM mistakes_made
            WHERE session_id = ? {tx_sql}
        """, (self.session_id, *tx_params))
        mistakes_count = cursor.fetchone()[0]

        # Unknown resolution ratio → know proxy (scope-weighted)
        # Floor at 0.3: logging unknowns shows domain awareness (knowing what
        # you don't know IS knowledge). Resolution further improves the score.
        # Without floor: 0 resolved = 0.0 which falsely signals "knows nothing"
        if unknowns_weighted_total > 0:
            raw_ratio = unknowns_weighted_resolved / unknowns_weighted_total
            resolution_ratio = 0.3 + (raw_ratio * 0.7)  # 0.3 (unresolved) → 1.0 (all resolved)
            items.append(EvidenceItem(
                source="artifacts",
                metric_name="unknown_resolution_ratio",
                value=resolution_ratio,
                raw_value={
                    "resolved_weighted": round(unknowns_weighted_resolved, 2),
                    "total_weighted": round(unknowns_weighted_total, 2),
                    "scoped_total": scoped_total,
                    "unscoped_total": unscoped_total,
                    "unscoped_weight": UNSCOPED_ARTIFACT_WEIGHT,
                    "raw_ratio": round(raw_ratio, 4),
                    "floor_applied": True,
                },
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["know"],
            ))

        # Productive exploration ratio → signal quality (scope-weighted)
        # (findings / (findings + dead_ends)) — higher = more productive
        total_exploration = findings_count + dead_ends_count
        if total_exploration > 0:
            productivity = findings_count / total_exploration
            items.append(EvidenceItem(
                source="artifacts",
                metric_name="productive_exploration_ratio",
                value=productivity,
                raw_value={
                    "findings_weighted": round(findings_count, 2),
                    "dead_ends_weighted": round(dead_ends_count, 2),
                },
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["signal", "know"],
            ))

        # Dead-end ratio — removed as direct uncertainty source (2026-04-07).
        # Uncertainty is now a META-derived quantity computed in mapper.py
        # from the grounded coverage and gap magnitudes of the OTHER 12
        # vectors. First-order doubt proxies like dead_end_ratio measured
        # something real (exploration productivity) but the mapping to
        # "how confident is the AI in its self-assessment overall" was
        # ad-hoc. Dead-end ratio still contributes to signal/know via
        # productive_exploration_ratio above.

        # Mistake density → inverse signal (uses raw findings for denominator)
        raw_findings = (scoped_findings + unscoped_findings) if has_goals else findings_count
        if raw_findings > 0:
            mistake_ratio = mistakes_count / (raw_findings + mistakes_count)
            items.append(EvidenceItem(
                source="artifacts",
                metric_name="mistake_ratio",
                value=1.0 - mistake_ratio,  # Invert: fewer mistakes = better
                raw_value={"mistakes": mistakes_count, "findings": raw_findings},
                quality=EvidenceQuality.INFERRED,
                supports_vectors=["signal"],
            ))

        # ── Context evidence: project/session-level knowledge availability ──
        # These are phase-agnostic (run in both noetic and praxic phases)
        # because they measure AVAILABLE context, not phase-specific activity.

        # Project epistemic depth: prior artifacts from other sessions.
        # Rich project history = richer context available via PREFLIGHT,
        # project-search, and Qdrant retrieval.
        try:
            prior_artifact_count = 0
            for table in ("project_findings", "project_unknowns",
                          "project_dead_ends", "decisions", "assumptions"):
                try:
                    cursor.execute(f"""
                        SELECT COUNT(*) FROM {table}
                        WHERE session_id != ?
                    """, (self.session_id,))
                    prior_artifact_count += cursor.fetchone()[0]
                except Exception:
                    continue

            if prior_artifact_count > 0:
                depth = min(1.0, prior_artifact_count / 100.0)
                items.append(EvidenceItem(
                    source="artifacts",
                    metric_name="project_epistemic_depth",
                    value=depth,
                    raw_value={"prior_artifacts": prior_artifact_count},
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["context"],
                ))

            # Session accumulated context: completed transactions in this session.
            # More completed transactions = deeper understanding accumulated.
            cursor.execute("""
                SELECT COUNT(*) FROM reflexes
                WHERE session_id = ? AND phase = 'POSTFLIGHT'
            """, (self.session_id,))
            completed_txns = cursor.fetchone()[0]

            if completed_txns > 0:
                session_context = min(1.0, 0.2 + completed_txns * 0.2)
                items.append(EvidenceItem(
                    source="artifacts",
                    metric_name="session_accumulated_context",
                    value=session_context,
                    raw_value={"completed_transactions": completed_txns},
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["context"],
                ))

            # PREFLIGHT pattern count: how many prior-knowledge patterns were
            # loaded at PREFLIGHT? Stored in transaction file by PREFLIGHT handler.
            # More patterns = richer task-relevant context available.
            try:
                from empirica.utils.session_resolver import _get_instance_suffix
                suffix = _get_instance_suffix()
                project_root = self._resolve_project_root()
                if project_root:
                    tx_file = Path(project_root) / '.empirica' / f'active_transaction{suffix}.json'
                    if tx_file.exists():
                        with open(tx_file) as f:
                            tx_data = json.load(f)
                        pattern_count = tx_data.get('preflight_pattern_count', 0)
                        if pattern_count > 0:
                            # Normalize: 5 patterns = 0.33, 15+ = 1.0
                            richness = min(1.0, pattern_count / 15.0)
                            items.append(EvidenceItem(
                                source="artifacts",
                                metric_name="preflight_context_richness",
                                value=richness,
                                raw_value={"pattern_count": pattern_count},
                                quality=EvidenceQuality.SEMI_OBJECTIVE,
                                supports_vectors=["context", "know"],
                            ))
            except Exception:
                pass  # Non-fatal — pattern count may not be available
        except Exception:
            pass  # Non-fatal

        return items

    def _collect_issue_metrics(self) -> list[EvidenceItem]:
        """Collect auto-captured issues for this session.

        Maturity-aware: greenfield projects with zero issues produce no evidence
        rather than a floored 0.2, because absence of issues in a new project
        is not meaningful signal for impact.
        """
        items = []
        db = self._get_db()
        cursor = db.conn.cursor()

        tx_sql, tx_params = self._tx_filter()
        try:
            cursor.execute(f"""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) as resolved,
                    SUM(CASE WHEN severity = 'blocker' OR severity = 'high' THEN 1 ELSE 0 END) as severe
                FROM auto_captured_issues
                WHERE session_id = ? {tx_sql}
            """, (self.session_id, *tx_params))
            row = cursor.fetchone()
        except Exception:
            return items

        if row and row[0] > 0:
            total, resolved, severe = row[0], row[1] or 0, row[2] or 0

            # Issue resolution ratio → impact proxy
            # Floor at 0.2: capturing issues shows situational awareness (like
            # logging unknowns shows domain awareness). Resolution improves score.
            # Without floor: 0 resolved = 0.0 which falsely signals "no impact"
            if total > 0:
                raw_ratio = resolved / total
                resolution_ratio = 0.2 + (raw_ratio * 0.8)  # 0.2 (unresolved) → 1.0 (all resolved)
                items.append(EvidenceItem(
                    source="issues",
                    metric_name="issue_resolution_ratio",
                    value=resolution_ratio,
                    raw_value={"resolved": resolved, "total": total, "raw_ratio": round(raw_ratio, 4), "floor_applied": True},
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["impact"],
                ))

            # Inverse severe issue density → signal quality
            # Fewer severe issues = better signal quality
            severity_score = max(0.0, 1.0 - (severe / max(total, 1)))
            items.append(EvidenceItem(
                source="issues",
                metric_name="inverse_severe_issue_density",
                value=severity_score,
                raw_value={"severe": severe, "total": total},
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["signal"],
            ))
        else:
            # No issues at all — only provide evidence for mature projects
            # where zero issues is meaningful. For greenfield/young projects,
            # absence of issues is not informative (no code to have issues with).
            maturity = self._detect_project_maturity()
            if not maturity["is_greenfield"] and not maturity["is_young"]:
                items.append(EvidenceItem(
                    source="issues",
                    metric_name="issue_resolution_ratio",
                    value=1.0,
                    raw_value={"resolved": 0, "total": 0, "note": "no issues in mature project"},
                    quality=EvidenceQuality.INFERRED,
                    supports_vectors=["impact"],
                ))

        return items

    def _collect_triage_metrics(self) -> list[EvidenceItem]:
        """Collect evidence from epistemic triage work during this session.

        Triage is praxic work that doesn't produce code artifacts:
        - Completing goals (via goals-complete) → do, completion
        - Resolving unknowns (via unknown-resolve) → do, know
        - Logging findings → know

        Uses timestamps to capture work done DURING this session, regardless
        of which session originally created the artifact. A goal created in
        session A but completed in session B should count as session B's work.

        This ensures that triage sessions (cleaning unknowns, completing goals,
        organizing artifacts) get proper `do` grounding instead of 0.0.
        """
        items = []

        # Transaction-scoping requirement (2026-04-07, second pass):
        #
        # Session-scoping is meaningless for metric calculation. Sessions exist
        # only for Claude Code continuity across compactions and for attaching
        # instance state to the right transaction — they are NOT a valid unit
        # for measuring "what work got done". Metrics must always be scoped by
        # transaction (via preflight_timestamp), with the broader project as
        # the only other meaningful boundary.
        #
        # Without preflight_timestamp we cannot honestly bound "since when",
        # so we emit no evidence items rather than falling back to session
        # scope (which would produce the session-wide totals that caused the
        # ~0.51 constant-offset completion bias in session 659f0619).
        if self.preflight_timestamp is None:
            return items

        scope_start = self.preflight_timestamp
        db = self._get_db()
        cursor = db.conn.cursor()

        # Goals completed since scope_start (transaction or session)
        cursor.execute("""
            SELECT COUNT(*) FROM goals
            WHERE status = 'completed'
              AND completed_timestamp >= ?
              AND completed_timestamp IS NOT NULL
        """, (scope_start,))
        goals_completed = cursor.fetchone()[0]

        # Total goals relevant to THIS transaction (for ratio):
        # Only count goals that were TOUCHED in this transaction:
        #   - Created during this transaction (created_timestamp >= scope_start)
        #   - Completed during this transaction (completed_timestamp >= scope_start)
        #   - Linked to this transaction via transaction_id
        # This prevents historical goals from inflating the denominator
        # (the 1/18 bug where 17 old open goals dilute a 1/1 completion).
        # Exclude 'planned' goals — they exist but haven't been started yet.
        if self.transaction_id:
            cursor.execute("""
                SELECT COUNT(DISTINCT id) FROM goals
                WHERE status != 'planned'
                  AND (transaction_id = ?
                   OR created_timestamp >= ?
                   OR (completed_timestamp >= ? AND completed_timestamp IS NOT NULL))
            """, (self.transaction_id, scope_start, scope_start))
        else:
            cursor.execute("""
                SELECT COUNT(DISTINCT id) FROM goals
                WHERE status != 'planned'
                  AND (created_timestamp >= ?
                   OR (completed_timestamp >= ? AND completed_timestamp IS NOT NULL))
            """, (scope_start, scope_start))
        total_goals_in_scope = max(cursor.fetchone()[0], goals_completed)

        scope_label = "transaction"

        if goals_completed > 0:
            # Goal completion → do (completing goals = doing work).
            # Transaction-scoped expectation: 1-3 goals per transaction is normal.
            # Maps 1→0.7, 2→0.9, 3+→1.0
            do_score = min(1.0, 0.5 + goals_completed * 0.2)
            items.append(EvidenceItem(
                source="triage",
                metric_name="goals_completed",
                value=do_score,
                raw_value={
                    "completed": goals_completed,
                    "in_scope": total_goals_in_scope,
                    "scope": scope_label,
                },
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["do", "completion"],
                metadata={"work_type": "triage"},
            ))

            # Completion ratio (if meaningful denominator)
            if total_goals_in_scope > 0:
                completion_ratio = min(1.0, goals_completed / total_goals_in_scope)
                items.append(EvidenceItem(
                    source="triage",
                    metric_name="goal_completion_ratio",
                    value=completion_ratio,
                    raw_value={
                        "completed": goals_completed,
                        "in_scope": total_goals_in_scope,
                        "scope": scope_label,
                    },
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["completion", "change"],
                    metadata={"work_type": "triage"},
                ))

        # Unknowns resolved since scope_start
        cursor.execute("""
            SELECT COUNT(*) FROM project_unknowns
            WHERE is_resolved = 1
              AND resolved_timestamp >= ?
              AND resolved_timestamp IS NOT NULL
        """, (scope_start,))
        unknowns_resolved = cursor.fetchone()[0]

        if unknowns_resolved > 0:
            # Resolving unknowns IS doing work — epistemic action.
            # Transaction-scoped: 1 resolution = strong signal.
            # Maps 1→0.7, 2→0.9, 3+→1.0
            resolve_do_score = min(1.0, 0.5 + unknowns_resolved * 0.2)
            items.append(EvidenceItem(
                source="triage",
                metric_name="unknowns_resolved",
                value=resolve_do_score,
                raw_value={
                    "resolved": unknowns_resolved,
                    "scope": scope_label,
                },
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["do", "know"],
                metadata={"work_type": "triage"},
            ))

            # High resolution count signals change happened.
            # Transaction-scoped: 2+ resolutions is substantial.
            if unknowns_resolved >= 2:
                change_score = min(1.0, unknowns_resolved / 4.0)
                items.append(EvidenceItem(
                    source="triage",
                    metric_name="triage_change",
                    value=change_score,
                    raw_value={
                        "unknowns_resolved": unknowns_resolved,
                        "scope": scope_label,
                    },
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["change"],
                    metadata={"work_type": "triage"},
                ))

        return items

    def _collect_codebase_model_metrics(self) -> list[EvidenceItem]:
        """Collect codebase entity graph metrics for grounded calibration.

        Measures structural understanding of the codebase via entity extraction:
        - Entities discovered/updated during this session → know, context
        - Entity invalidations (deleted code) → change
        - Constraints (learned conventions) → coherence, signal

        Only runs if codebase_model tables exist (migration 033+).
        """
        items = []
        db = self._get_db()
        cursor = db.conn.cursor()

        # Check if codebase_entities table exists
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='codebase_entities'"
        )
        if not cursor.fetchone():
            return items

        # Entities linked to this session
        try:
            entity_stats = db.codebase_model.session_entity_stats(self.session_id)
        except Exception:
            return items

        total_entities = sum(entity_stats.values())
        if total_entities > 0:
            # Entity discovery → know (understanding of codebase structure)
            # Normalize: 5 entities = 0.3, 20 = 0.6, 50+ = 1.0
            know_score = min(1.0, total_entities / 50.0)
            items.append(EvidenceItem(
                source="codebase_model",
                metric_name="entities_discovered",
                value=know_score,
                raw_value={
                    "total": total_entities,
                    "by_type": entity_stats,
                },
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["know", "context"],
                metadata={"work_type": "entity_extraction"},
            ))

        # Facts created this session
        try:
            fact_count = db.codebase_model.session_fact_count(self.session_id)
        except Exception:
            fact_count = 0

        if fact_count > 0:
            # Facts → signal (understanding of what changed and why)
            signal_score = min(1.0, fact_count / 20.0)
            items.append(EvidenceItem(
                source="codebase_model",
                metric_name="facts_created",
                value=signal_score,
                raw_value={"count": fact_count},
                quality=EvidenceQuality.SEMI_OBJECTIVE,
                supports_vectors=["signal", "density"],
                metadata={"work_type": "entity_extraction"},
            ))

        # Active constraints for the project → coherence (learned conventions)
        if self.project_id:
            try:
                constraints = db.codebase_model.get_constraints(
                    project_id=self.project_id
                )
                if constraints:
                    total_violations = sum(c.get('violation_count', 0) for c in constraints)
                    # More constraints with violations = better convention awareness
                    coherence_score = min(1.0, len(constraints) / 10.0)
                    items.append(EvidenceItem(
                        source="codebase_model",
                        metric_name="convention_constraints",
                        value=coherence_score,
                        raw_value={
                            "constraint_count": len(constraints),
                            "total_violations": total_violations,
                        },
                        quality=EvidenceQuality.SEMI_OBJECTIVE,
                        supports_vectors=["coherence", "signal"],
                        metadata={"work_type": "entity_extraction"},
                    ))
            except Exception:
                pass

        return items

    def _gather_non_git_files(self, git_root: str | None) -> set:
        """Gather file paths edited outside the git repository from transaction and DB sources."""
        non_git_files: set = set()
        for fp in self._get_transaction_edited_files():
            if self._is_outside_git(fp, git_root):
                non_git_files.add(fp)
        try:
            db = self._get_db()
            cursor = db.conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='codebase_entities'")
            if cursor.fetchone():
                cursor.execute("SELECT DISTINCT file_path FROM codebase_entities WHERE session_id = ? AND file_path IS NOT NULL", (self.session_id,))
                for row in cursor.fetchall():
                    if row[0] and self._is_outside_git(row[0], git_root):
                        non_git_files.add(row[0])
        except Exception:
            pass
        return non_git_files

    def _verify_recently_modified(self, file_paths: set) -> list[str]:
        """Filter file paths to those that exist and were modified after transaction start."""
        existing_files = []
        tx_start = self.preflight_timestamp or self.check_timestamp
        for fp in file_paths:
            p = Path(fp)
            if p.exists():
                if tx_start:
                    try:
                        if p.stat().st_mtime >= (tx_start - 1.0):
                            existing_files.append(fp)
                    except OSError:
                        pass
                else:
                    existing_files.append(fp)
        return existing_files

    def _collect_non_git_file_metrics(self) -> list[EvidenceItem]:
        """Collect evidence of file changes outside the git repository.

        When work happens in directories that aren't git-tracked (e.g.
        ~/.claude/plugins/, config files outside repos), git metrics see
        zero changes and grounded calibration penalizes state/change/do.

        This collector reads:
        1. edited_files from the active transaction file (set by Sentinel)
        2. file_path from codebase_entities for this session

        Files inside the git repo are excluded (already covered by git metrics).
        """
        items = []
        project_root = self._resolve_project_root()

        # Determine git root for filtering
        git_root = None
        if project_root:
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True, timeout=5,
                    cwd=project_root,
                )
                if result.returncode == 0:
                    git_root = result.stdout.strip()
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass

        non_git_files = self._gather_non_git_files(git_root)
        if not non_git_files:
            return items

        existing_files = self._verify_recently_modified(non_git_files)
        if not existing_files:
            return items

        file_count = len(existing_files)

        # Normalize: 1-2 files = 0.3, 3-5 = 0.6, 6+ = 0.8+
        change_score = min(1.0, 0.15 * file_count + 0.15)

        items.append(EvidenceItem(
            source="non_git_files",
            metric_name="files_edited_outside_git",
            value=change_score,
            raw_value={
                "files": file_count,
                "paths": existing_files[:20],  # Cap for storage
                "git_root": git_root or "none",
            },
            quality=EvidenceQuality.SEMI_OBJECTIVE,
            supports_vectors=["state", "change", "do"],
            metadata={"work_type": "non_git_file_edit"},
        ))

        return items

    def _get_transaction_edited_files(self) -> list[str]:
        """Read edited_files list from the hook counters JSON.

        The Sentinel's _try_increment_tool_count appends file_path for
        every Edit/Write tool call to the hook_counters file (since v1.8.14).
        Falls back to reading from active_transaction for backward compat.
        """
        from empirica.utils.session_resolver import InstanceResolver as R

        suffix = R.instance_suffix()
        project_root = self._resolve_project_root()

        # Try hook_counters file first (v1.8.14+), then fall back to transaction file
        search_dirs = []
        if project_root:
            search_dirs.append(Path(project_root) / '.empirica')
        search_dirs.append(Path.home() / '.empirica')

        for empirica_dir in search_dirs:
            # Primary: hook_counters file (hook-owned since v1.8.14)
            counters_path = empirica_dir / f'hook_counters{suffix}.json'
            if counters_path.exists():
                try:
                    with open(counters_path) as f:
                        return json.load(f).get('edited_files', [])
                except Exception:
                    pass
            # Fallback: active_transaction (pre-v1.8.14 compat)
            tx_path = empirica_dir / f'active_transaction{suffix}.json'
            if tx_path.exists():
                try:
                    with open(tx_path) as f:
                        tx = json.load(f)
                    if 'edited_files' in tx:
                        return tx.get('edited_files', [])
                except Exception:
                    pass
        return []

    @staticmethod
    def _is_outside_git(file_path: str, git_root: str | None) -> bool:
        """Check if a file path is outside the git repository."""
        if not git_root:
            return True  # No git root = everything is non-git
        try:
            resolved = str(Path(file_path).resolve())
            git_resolved = str(Path(git_root).resolve())
            return not (resolved == git_resolved or resolved.startswith(git_resolved + '/'))
        except (OSError, ValueError):
            return True

    def _collect_sentinel_metrics(self) -> list[EvidenceItem]:
        """Collect sentinel gate decisions for this transaction."""
        items = []
        db = self._get_db()
        cursor = db.conn.cursor()
        tx_sql, tx_params = self._tx_filter()

        # CHECK phase decisions
        cursor.execute(f"""
            SELECT reflex_data FROM reflexes
            WHERE session_id = ? AND phase = 'CHECK' {tx_sql}
            ORDER BY timestamp DESC
        """, (self.session_id, *tx_params))
        rows = cursor.fetchall()

        if rows:
            proceed_count = 0
            investigate_count = 0
            for row in rows:
                try:
                    data = json.loads(row[0]) if row[0] else {}
                    decision = data.get("decision", "")
                    if decision == "proceed":
                        proceed_count += 1
                    elif decision == "investigate":
                        investigate_count += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            total_checks = proceed_count + investigate_count
            if total_checks > 0:
                proceed_ratio = proceed_count / total_checks
                items.append(EvidenceItem(
                    source="sentinel",
                    metric_name="check_proceed_ratio",
                    value=proceed_ratio,
                    raw_value={"proceed": proceed_count, "investigate": investigate_count},
                    quality=EvidenceQuality.SEMI_OBJECTIVE,
                    supports_vectors=["context"],
                ))

            # Investigation rounds — removed as direct uncertainty source (2026-04-07).
            # Uncertainty is now a META-derived quantity computed in
            # mapper.py from the coverage and gap magnitudes of the OTHER
            # 12 vectors. Number of check rounds is tangentially related
            # but the mapping to "overall confidence in self-assessment"
            # was ad-hoc. Check-round count is still observable via
            # check_proceed_ratio above for context scoring.

        return items

    def _collect_test_results(self) -> list[EvidenceItem]:
        """Collect pytest results from JSON report if available."""
        items = []
        root = Path(self._resolve_project_root() or ".")

        # Look for pytest JSON report in standard locations
        report_paths = [
            root / ".empirica" / "pytest_report.json",
            root / "pytest_report.json",
            root / ".pytest_report.json",
            root / "htmlcov" / "status.json",
        ]

        report = None
        for path in report_paths:
            if path.exists():
                try:
                    report = json.loads(path.read_text())
                    break
                except (json.JSONDecodeError, OSError):
                    continue

        if report is None:
            return items

        # Parse pytest-json-report format
        summary = report.get("summary", {})
        passed = summary.get("passed", 0)
        failed = summary.get("failed", 0)
        total = summary.get("total", passed + failed)

        if total > 0:
            pass_rate = passed / total
            items.append(EvidenceItem(
                source="pytest",
                metric_name="test_pass_rate",
                value=pass_rate,
                raw_value={"passed": passed, "failed": failed, "total": total},
                quality=EvidenceQuality.OBJECTIVE,
                supports_vectors=["know", "do", "state"],
            ))

        # Coverage data (if present via pytest-cov JSON)
        coverage_paths = [
            root / "coverage.json",
            root / ".coverage.json",
            root / "htmlcov" / "status.json",
        ]

        for cov_path in coverage_paths:
            if cov_path.exists():
                try:
                    cov_data = json.loads(cov_path.read_text())
                    total_pct = cov_data.get("totals", {}).get("percent_covered", 0)
                    if total_pct > 0:
                        items.append(EvidenceItem(
                            source="pytest",
                            metric_name="test_coverage_percent",
                            value=total_pct / 100.0,
                            raw_value={"percent": total_pct},
                            quality=EvidenceQuality.OBJECTIVE,
                            supports_vectors=["clarity", "know"],
                        ))
                    break
                except (json.JSONDecodeError, OSError):
                    continue

        return items

    def _get_transaction_since(self) -> str | None:
        """Get the transaction start timestamp as a git --since argument.

        Priority: preflight_timestamp > check_timestamp (praxic) > session start.
        Returns "@<unix_timestamp>" string or None.
        """
        # For praxic phase, scope from CHECK (when action started)
        if self.phase == "praxic" and self.check_timestamp:
            return "@" + str(int(self.check_timestamp))
        # For combined/noetic, scope from PREFLIGHT (transaction start)
        if self.preflight_timestamp:
            return "@" + str(int(self.preflight_timestamp))
        # Fallback: session start
        db = self._get_db()
        cursor = db.conn.cursor()
        cursor.execute(
            "SELECT start_time FROM sessions WHERE session_id = ?",
            (self.session_id,),
        )
        row = cursor.fetchone()
        if row:
            try:
                return "@" + str(int(float(str(row[0]))))
            except (ValueError, TypeError):
                pass
        return None

    def _collect_git_loc_amd_tree(self, since: str, project_root: str) -> list[EvidenceItem]:
        """Collect LOC delta, A/M/D file ratio, and working-tree cleanliness evidence."""
        import re
        items = []
        result = subprocess.run(["git", "log", "--shortstat", "--format=", "--since=" + since],
                                capture_output=True, text=True, timeout=5, cwd=project_root)
        if result.returncode == 0 and result.stdout.strip():
            insertions = sum(int(m.group(1)) for m in re.finditer(r'(\d+) insertion', result.stdout))
            deletions = sum(int(m.group(1)) for m in re.finditer(r'(\d+) deletion', result.stdout))
            total_loc = insertions + deletions
            if total_loc > 0:
                items.append(EvidenceItem(source="git", metric_name="loc_delta", value=min(1.0, total_loc / 500.0),
                    raw_value={"insertions": insertions, "deletions": deletions, "total": total_loc},
                    quality=EvidenceQuality.OBJECTIVE, supports_vectors=["change", "do"]))
        amd_counts = {}
        for filter_code, label in [("A", "added"), ("M", "modified"), ("D", "deleted")]:
            result = subprocess.run(["git", "log", "--name-only", "--format=", f"--diff-filter={filter_code}", "--since=" + since],
                                    capture_output=True, text=True, timeout=5, cwd=project_root)
            amd_counts[label] = len({f.strip() for f in result.stdout.strip().split('\n') if f.strip()}) if result.returncode == 0 else 0
        total_amd = sum(amd_counts.values())
        if total_amd > 0:
            weighted = amd_counts["added"] * 1.0 + amd_counts["modified"] * 0.5 + amd_counts["deleted"] * 0.3
            items.append(EvidenceItem(source="git", metric_name="amd_file_ratio", value=min(1.0, weighted / max(total_amd, 1)),
                raw_value=amd_counts, quality=EvidenceQuality.OBJECTIVE, supports_vectors=["state", "change"]))
        result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, timeout=5, cwd=project_root)
        if result.returncode == 0:
            uncommitted = len([l for l in result.stdout.strip().split('\n') if l.strip()])
            items.append(EvidenceItem(source="git", metric_name="working_tree_cleanliness",
                value=max(0.0, 1.0 - min(1.0, uncommitted / 10.0)),
                raw_value={"uncommitted_files": uncommitted}, quality=EvidenceQuality.OBJECTIVE, supports_vectors=["state"]))
        return items

    def _collect_git_metrics(self) -> list[EvidenceItem]:
        """Collect git-based metrics scoped to the current transaction.

        Transaction-scoped: uses PREFLIGHT timestamp (or CHECK for praxic)
        as the --since boundary, not a fixed HEAD~N window.

        Modular change density: instead of files_changed / fixed_divisor,
        computes files_touched / total_files_in_touched_directories.
        This captures "how much did this transaction reshape the modules
        it worked in?" rather than "what fraction of the whole codebase?".
        """
        items = []
        project_root = self._resolve_project_root()
        maturity = self._detect_project_maturity()

        since = self._get_transaction_since()
        if not since:
            return items

        try:
            # Count commits since transaction start
            result = subprocess.run(
                ["git", "log", "--oneline", "--since=" + since, "--format=%H"],
                capture_output=True, text=True, timeout=5,
                cwd=project_root,
            )
            if result.returncode == 0:
                commits = [c for c in result.stdout.strip().split('\n') if c]
                commit_count = len(commits)

                if commit_count > 0:
                    # Maturity-aware normalization for commit count
                    if maturity["is_greenfield"]:
                        do_score = min(1.0, commit_count / 2.0)
                    elif maturity["is_young"]:
                        do_score = min(1.0, commit_count / 3.0)
                    else:
                        do_score = min(1.0, commit_count / 5.0)

                    items.append(EvidenceItem(
                        source="git",
                        metric_name="commit_count",
                        value=do_score,
                        raw_value={"commits": commit_count,
                                   "maturity": maturity["maturity"]},
                        quality=EvidenceQuality.OBJECTIVE,
                        supports_vectors=["do", "change"],
                    ))

            # Files changed since transaction start (transaction-scoped)
            result = subprocess.run(
                ["git", "log", "--name-only", "--format=",
                 "--diff-filter=ACMR", "--since=" + since],
                capture_output=True, text=True, timeout=5,
                cwd=project_root,
            )
            if result.returncode == 0:
                changed_files = {
                    f.strip() for f in result.stdout.strip().split('\n')
                    if f.strip()
                }
                # Also include staged/unstaged changes
                for diff_args in [["--cached"], []]:
                    staged = subprocess.run(
                        ["git", "diff", "--name-only", "--diff-filter=ACMR"] + diff_args,
                        capture_output=True, text=True, timeout=5,
                        cwd=project_root,
                    )
                    if staged.returncode == 0:
                        changed_files.update(
                            f.strip() for f in staged.stdout.strip().split('\n')
                            if f.strip()
                        )

                files_changed = len(changed_files)
                if files_changed > 0:
                    # Modular change density: files touched / total files in touched dirs
                    change_density = self._compute_change_density(
                        changed_files, project_root,
                    )

                    items.append(EvidenceItem(
                        source="git",
                        metric_name="files_changed",
                        value=change_density["state_score"],
                        raw_value={
                            "files": files_changed,
                            "module_files": change_density["module_files"],
                            "density": change_density["density"],
                            "modules": change_density["modules_touched"],
                            "maturity": maturity["maturity"],
                        },
                        quality=EvidenceQuality.OBJECTIVE,
                        supports_vectors=["state", "change"],
                    ))

            # LOC, AMD, and working-tree evidence
            items.extend(self._collect_git_loc_amd_tree(since, project_root))

        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass

        return items

    def _compute_change_density(
        self,
        changed_files: set,
        project_root: str,
    ) -> dict[str, Any]:
        """Compute modular change density: files touched / files in touched dirs.

        Instead of normalizing against the whole codebase (files/10), we measure
        how much the touched modules were reshaped. Touching 12/30 files across
        5 directories = 0.4 density, which better reflects variance of work than
        12/total_project_files.

        Returns dict with density, state_score, and metadata.
        """
        root = Path(project_root)

        # Find unique parent directories of changed files
        touched_dirs: set = set()
        for f in changed_files:
            parent = str(Path(f).parent)
            if parent == '.':
                parent = ''
            touched_dirs.add(parent)

        # Count total tracked files in those directories (non-recursive per dir)
        module_files = 0
        for d in touched_dirs:
            dir_path = root / d if d else root
            if dir_path.is_dir():
                # Count files that git tracks in this directory (non-recursive)
                try:
                    result = subprocess.run(
                        ["git", "ls-files", str(d) + "/" if d else "."],
                        capture_output=True, text=True, timeout=5,
                        cwd=project_root,
                    )
                    if result.returncode == 0:
                        # Filter to direct children only (not recursive)
                        dir_prefix = d + "/" if d else ""
                        direct_files = [
                            f for f in result.stdout.strip().split('\n')
                            if f.strip() and f.count('/') == dir_prefix.count('/')
                        ]
                        module_files += len(direct_files)
                except (subprocess.TimeoutExpired, FileNotFoundError):
                    pass

        # Fallback: if we couldn't count module files, use maturity-based divisor
        if module_files == 0:
            maturity = self._detect_project_maturity()
            if maturity["is_greenfield"]:
                divisor = 3.0
            elif maturity["is_young"]:
                divisor = 5.0
            else:
                divisor = 10.0
            density = min(1.0, len(changed_files) / divisor)
        else:
            density = min(1.0, len(changed_files) / max(module_files, 1))

        return {
            "density": round(density, 4),
            "state_score": density,
            "module_files": module_files,
            "modules_touched": len(touched_dirs),
        }

    def _collect_ruff_evidence(self, py_files: list[str], project_root: str) -> list[EvidenceItem]:
        """Run ruff on changed Python files and return clarity/coherence/signal evidence."""
        items = []
        try:
            result = subprocess.run(["ruff", "check", "--output-format", "json", "--quiet"] + py_files,
                                    capture_output=True, text=True, timeout=30, cwd=project_root)
            if result.stdout.strip():
                import json as _json
                violations = _json.loads(result.stdout)
                violation_count = len(violations)
                lines_total = self._count_lines(py_files)
                if lines_total > 0:
                    density_per_100 = (violation_count / lines_total) * 100
                    clarity_score = max(0.0, 1.0 - (density_per_100 / 10.0))
                    items.append(EvidenceItem(source="code_quality", metric_name="ruff_violation_density",
                        value=clarity_score, raw_value={"violations": violation_count, "lines": lines_total,
                            "per_100_lines": round(density_per_100, 2), "files_checked": len(py_files)},
                        quality=EvidenceQuality.SEMI_OBJECTIVE, supports_vectors=["clarity", "coherence"]))
                    error_count = sum(1 for v in violations if v.get("code", "").startswith(("E", "F")))
                    if violation_count > 0:
                        items.append(EvidenceItem(source="code_quality", metric_name="ruff_error_ratio",
                            value=max(0.0, 1.0 - error_count / violation_count),
                            raw_value={"errors": error_count, "style": violation_count - error_count, "total": violation_count},
                            quality=EvidenceQuality.SEMI_OBJECTIVE, supports_vectors=["signal"]))
            elif result.returncode == 0:
                items.append(EvidenceItem(source="code_quality", metric_name="ruff_violation_density", value=1.0,
                    raw_value={"violations": 0, "files_checked": len(py_files)},
                    quality=EvidenceQuality.SEMI_OBJECTIVE, supports_vectors=["clarity", "coherence"]))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass
        return items

    def _collect_code_quality_metrics(self) -> list[EvidenceItem]:
        """Collect code quality evidence from static analysis tools.

        Runs ruff, radon, and pyright on files changed during this session.
        These provide OBJECTIVE evidence for vectors previously considered
        ungroundable (density, coherence) plus additional grounding for
        clarity, know, do, and signal.

        Tool availability is detected at runtime — missing tools are skipped.
        """
        items = []
        project_root = self._resolve_project_root()

        # Get files changed during this session from git
        changed_files = self._get_session_changed_files()
        if not changed_files:
            return items

        py_files = [f for f in changed_files if f.endswith('.py')]
        if not py_files:
            return items

        # --- Ruff: linting violations → clarity, coherence ---
        items.extend(self._collect_ruff_evidence(py_files, project_root))

        # --- Radon: cyclomatic complexity → density, signal ---
        try:
            result = subprocess.run(
                ["radon", "cc", "-s", "-a", "-j"] + py_files,
                capture_output=True, text=True, timeout=30,
                cwd=project_root,
            )
            if result.returncode == 0 and result.stdout.strip():
                import json as _json
                cc_data = _json.loads(result.stdout)

                all_complexities = []
                high_complexity_count = 0  # CC >= 11 (grade C or worse)
                for _, functions in cc_data.items():
                    for func in functions:
                        cc = func.get("complexity", 0)
                        all_complexities.append(cc)
                        if cc >= 11:
                            high_complexity_count += 1

                if all_complexities:
                    avg_cc = sum(all_complexities) / len(all_complexities)
                    max_cc = max(all_complexities)

                    # Normalize avg complexity: 1-5 = 1.0, 20+ = 0.0
                    density_score = max(0.0, min(1.0, 1.0 - (avg_cc - 5) / 15.0))
                    items.append(EvidenceItem(
                        source="code_quality",
                        metric_name="radon_avg_complexity",
                        value=density_score,
                        raw_value={
                            "avg_cc": round(avg_cc, 1),
                            "max_cc": max_cc,
                            "functions_analyzed": len(all_complexities),
                            "high_complexity_count": high_complexity_count,
                        },
                        quality=EvidenceQuality.SEMI_OBJECTIVE,
                        supports_vectors=["density", "signal"],
                    ))
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        # --- Pyright: type errors → know, do ---
        try:
            result = subprocess.run(
                ["pyright", "--outputjson"] + py_files,
                capture_output=True, text=True, timeout=60,
                cwd=project_root,
            )
            if result.stdout.strip():
                import json as _json
                try:
                    pyright_data = _json.loads(result.stdout)
                    summary = pyright_data.get("summary", {})
                    error_count = summary.get("errorCount", 0)
                    warning_count = summary.get("warningCount", 0)
                    files_analyzed = summary.get("filesAnalyzed", len(py_files))

                    if files_analyzed > 0:
                        # Errors per file — lower is better
                        errors_per_file = error_count / files_analyzed
                        # Normalize: 0 errors = 1.0, 5+ per file = 0.0
                        type_safety_score = max(0.0, 1.0 - (errors_per_file / 5.0))
                        items.append(EvidenceItem(
                            source="code_quality",
                            metric_name="pyright_type_safety",
                            value=type_safety_score,
                            raw_value={
                                "errors": error_count,
                                "warnings": warning_count,
                                "files_analyzed": files_analyzed,
                                "errors_per_file": round(errors_per_file, 2),
                            },
                            quality=EvidenceQuality.SEMI_OBJECTIVE,
                            supports_vectors=["know", "do"],
                        ))
                except (json.JSONDecodeError, KeyError):
                    pass
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

        return items

    def _get_session_changed_files(self) -> list[str]:
        """Get files changed during this transaction/session via git.

        Combines three sources:
        1. Committed changes since transaction start (git log --since)
        2. Staged but uncommitted changes (git diff --cached)
        3. Unstaged working tree changes (git diff)

        Uses transaction timestamp (PREFLIGHT/CHECK) when available,
        falls back to session start time.

        All git commands run with cwd=project_root so they query the correct repo.
        Returned paths are relative to project_root (as git outputs them).
        """
        all_files: set = set()
        project_root = self._resolve_project_root()

        # Prefer transaction-scoped timestamp
        since = self._get_transaction_since()
        if not since:
            # Fallback to session start
            db = self._get_db()
            cursor = db.conn.cursor()
            cursor.execute(
                "SELECT start_time FROM sessions WHERE session_id = ?",
                (self.session_id,),
            )
            row = cursor.fetchone()
            if row:
                try:
                    since = "@" + str(int(float(str(row[0]))))
                except (ValueError, TypeError):
                    pass

        try:
            # 1. Committed changes since transaction/session start
            if since:
                result = subprocess.run(
                    ["git", "log", "--name-only", "--format=",
                     "--diff-filter=ACMR", "--since=" + since],
                    capture_output=True, text=True, timeout=5,
                    cwd=project_root,
                )
                if result.returncode == 0:
                    all_files.update(f.strip() for f in result.stdout.strip().split('\n') if f.strip())

            # 2. Staged changes
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
                capture_output=True, text=True, timeout=5,
                cwd=project_root,
            )
            if result.returncode == 0:
                all_files.update(f.strip() for f in result.stdout.strip().split('\n') if f.strip())

            # 3. Unstaged working tree changes
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=ACMR"],
                capture_output=True, text=True, timeout=5,
                cwd=project_root,
            )
            if result.returncode == 0:
                all_files.update(f.strip() for f in result.stdout.strip().split('\n') if f.strip())

            # Fallback if nothing found: recent commits
            if not all_files:
                result = subprocess.run(
                    ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD~5..HEAD"],
                    capture_output=True, text=True, timeout=5,
                    cwd=project_root,
                )
                if result.returncode == 0:
                    all_files.update(f.strip() for f in result.stdout.strip().split('\n') if f.strip())

            # Filter to existing files only (paths are relative to project root)
            root = Path(project_root) if project_root else Path.cwd()
            return [f for f in all_files if (root / f).exists()]
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
            pass
        return []

    def _count_lines(self, file_paths: list[str]) -> int:
        """Count total lines across files (relative to project root)."""
        root = Path(self._resolve_project_root() or ".")
        total = 0
        for fp in file_paths:
            try:
                total += len((root / fp).read_text().splitlines())
            except OSError:
                pass
        return total

"""
Evidence-to-Vector Mapper

Maps objective evidence items to estimated vector values.
Only produces estimates for vectors with sufficient evidence.
Uses weighted aggregation when multiple evidence items support the same vector.

Calibration scoring uses domain-aware category weights (Tier 1) from
confidence_weights.yaml. Per-vector weights (Tier 2) can be passed in
from project.yaml for per-project, per-phase dynamic weighting.

Ungroundable vectors (no objective signal): engagement.
Coherence and density are now grounded via code quality metrics (ruff, radon).
These keep self-referential calibration.

NOTE on proxy limitations: All evidence sources here are deterministic proxies.
Test pass rates approximate 'know' but miss conceptual understanding depth.
Git metrics approximate 'do' but miss quality of thought behind changes.
Artifact counts approximate 'signal' but can't judge relevance of what was logged.
The calibration gaps computed here are drift indicators — useful for detecting
systematic bias patterns over time, but not ground truth for any single transaction.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .collector import EvidenceBundle, EvidenceItem, EvidenceQuality

logger = logging.getLogger(__name__)


QUALITY_WEIGHTS = {
    EvidenceQuality.OBJECTIVE: 1.0,
    EvidenceQuality.SEMI_OBJECTIVE: 0.7,
    EvidenceQuality.INFERRED: 0.4,
}

# Vectors with no objective signal. Code quality metrics (ruff, radon) now
# provide grounding for coherence and density, leaving only engagement.
UNGROUNDABLE_VECTORS = {"engagement"}

# Vectors computed as META-derivations from OTHER vectors rather than from
# direct evidence sources. These are not measured from collector artifacts;
# they are computed AFTER the other vectors are grounded, using the other
# vectors' coverage and gap magnitudes as input.
META_VECTORS = {"uncertainty"}

# Evidence source relevance by work_type.
#
# Keys are the actual source IDs emitted by PostTestCollector EvidenceItems:
#   noetic, goals, artifacts, issues, triage, codebase_model, non_git_files,
#   sentinel, pytest, git, code_quality
#
# Semantics:
#   missing source key   → defaults to 1.0 (full weight, neutral)
#   value == 0.0         → EXCLUDED. The source is instrument-blind for this
#                          work_type and is filtered out of the evidence pool
#                          entirely. Vectors that ONLY had evidence from
#                          excluded sources will be marked insufficient_evidence
#                          and will fall back to the AI's self-assessment
#                          (no fabricated grounded value, no false drift).
#   0.0 < value < 1.0    → down-weight (still contributes but reduced)
#   value > 1.0          → up-weight (primary evidence for this work_type)
#
# Design principle: when the instrument can't sample the work, report honest
# absence (None) rather than computing a misleading score from absent signal.
# See: docs/architecture/PHASE_AWARE_CALIBRATION.md and the metric-sycophancy
# discussion (2026-04-07).
#
# History: This dict previously used keys like "git_metrics", "test_results",
# "goal_completion", "artifact_counts" which DID NOT MATCH the source IDs
# emitted by collector.py — making the entire work_type scaling a silent no-op.
# Fixed 2026-04-07 along with adding the 0.0 = excluded semantic.
WORK_TYPE_RELEVANCE: dict[str, dict[str, float]] = {
    # code: baseline — all sources at default 1.0 weight
    "code": {},
    # infra: infrastructure changes (yaml, dockerfile, k8s manifests)
    # Code quality and pytest aren't great signals; goal completion is.
    "infra": {
        "code_quality": 0.3,
        "pytest": 0.3,
        "codebase_model": 0.3,
        "goals": 1.3,
        "git": 1.0,
        "artifacts": 1.1,
    },
    # research: experimental investigation, exploring possibilities
    # The deliverable is artifacts (findings, unknowns), not code.
    "research": {
        "git": 0.0,
        "code_quality": 0.0,
        "codebase_model": 0.0,
        "pytest": 0.0,
        "non_git_files": 0.0,
        "artifacts": 1.5,
        "goals": 1.0,
        "noetic": 1.5,
    },
    # release: publish pipeline (twine, docker, gh release, homebrew tap)
    # Git only sees mechanical version sweep commit. Code unchanged. Codebase
    # unchanged. The actual work is invisible to in-repo sensors.
    "release": {
        "git": 0.0,
        "code_quality": 0.0,
        "codebase_model": 0.0,
        "non_git_files": 0.0,
        "goals": 1.5,
        "triage": 1.3,
        "pytest": 1.2,
        "artifacts": 1.0,
    },
    # debug: finding + fixing bugs
    # Tests are the strongest signal (regression caught/passed).
    "debug": {
        "pytest": 1.4,
        "triage": 1.3,
        "artifacts": 1.3,
        "code_quality": 0.5,
        "git": 1.0,
    },
    # config: configuration changes (settings.json, .env, yaml)
    # Code quality + pytest are usually irrelevant; goal completion matters.
    "config": {
        "code_quality": 0.0,
        "pytest": 0.3,
        "codebase_model": 0.0,
        "goals": 1.2,
        "git": 1.0,
        "artifacts": 1.1,
    },
    # docs: documentation work (markdown, doc-only changes)
    # Markdown isn't ruff-checked; pytest doesn't apply; codebase model unchanged.
    "docs": {
        "code_quality": 0.0,
        "pytest": 0.0,
        "codebase_model": 0.0,
        "git": 1.0,
        "non_git_files": 1.2,
        "goals": 1.2,
        "artifacts": 1.0,
    },
    # data: data work (CSVs, datasets, schemas)
    "data": {
        "code_quality": 0.0,
        "pytest": 0.5,
        "codebase_model": 0.0,
        "non_git_files": 1.3,
        "goals": 1.2,
        "artifacts": 1.0,
    },
    # comms: writing messages, sending communications, outreach
    # No code touched, no tests, no codebase change.
    "comms": {
        "git": 0.0,
        "code_quality": 0.0,
        "pytest": 0.0,
        "codebase_model": 0.0,
        "non_git_files": 0.0,
        "goals": 1.5,
        "artifacts": 1.0,
    },
    # design: design docs, architecture proposals, mockups
    # Mostly markdown + diagrams, not code.
    "design": {
        "code_quality": 0.0,
        "pytest": 0.0,
        "codebase_model": 0.0,
        "git": 1.0,
        "non_git_files": 1.2,
        "artifacts": 1.4,
        "goals": 1.2,
    },
    # audit: read-only investigation (code review, security audit, doc audit)
    # Audits don't write code, don't change quality metrics, don't restructure.
    # The deliverable is artifacts (findings, decisions, recommendations).
    "audit": {
        "git": 0.0,
        "code_quality": 0.0,
        "codebase_model": 0.0,
        "non_git_files": 0.0,
        "artifacts": 1.5,
        "noetic": 1.3,
        "goals": 1.2,
        "triage": 1.0,
    },
    # remote-ops: work done on a machine the local Sentinel doesn't observe
    # (SSH sessions, customer machines, remote config, deploys without local
    # commits, on-site assistance). No source can ground vectors for this
    # work — every collector is set to 0.0 relevance, which routes every
    # vector that had any evidence to insufficient_evidence_vectors.
    # The AI's self-assessment stands unchallenged.
    #
    # FUTURE: a RemoteVerifier agent on target machines posting EvidenceItem
    # back via the dispatch bus will populate the artifacts source with real
    # data, at which point this relevance can be reintroduced.
    #
    # Note: every known collector source is listed explicitly so nothing
    # defaults to the 1.0 multiplier. Adding a new collector source means
    # adding it here too — the test_remote_ops_covers_all_known_collector_sources
    # regression test will catch the omission.
    "remote-ops": {
        "artifacts": 0.0,
        "noetic": 0.0,
        "sentinel": 0.0,
        "goals": 0.0,
        "issues": 0.0,
        "triage": 0.0,
        "codebase_model": 0.0,
        "non_git_files": 0.0,
        "git": 0.0,
        "code_quality": 0.0,
        "pytest": 0.0,
        "source_quality": 0.0,
        "prose_quality": 0.0,
        "document_metrics": 0.0,
        "action_verification": 0.0,
        "web": 0.0,
    },
}


@dataclass
class GroundedVectorEstimate:
    """An objectively grounded estimate for a single vector."""

    vector_name: str
    estimated_value: float
    confidence: float
    evidence_count: int
    primary_source: str
    is_grounded: bool = True


@dataclass
class GroundedAssessment:
    """Complete grounded assessment alongside self-assessment.

    Three-vector model (A3 Wave 1, SPEC 1 Part 3):
    - self_assessed: AI's PREFLIGHT/POSTFLIGHT vectors (unchanged)
    - grounded: service-computed observations mapped to vectors
      (NOTE: this field holds what SPEC 1 calls "observed" — the rename
      to an explicit observed/grounded split happens when B3 wires the
      AI-reasoned grounded state. Until then, the field name stays to
      avoid breaking 6+ consumers.)
    - grounded_rationale: AI's reasoning for divergence (B3 will populate)

    insufficient_evidence_vectors lists vector names that had ALL their
    evidence sources excluded for the current work_type — meaning the
    instrument is fundamentally blind to this kind of work for these vectors.

    calibration_status uses ComplianceStatus enum values (string-compatible).
    Only "grounded" and "complete" statuses write to learning_trajectory
    or feed previous_transaction_feedback.
    """

    session_id: str
    self_assessed: dict[str, float]
    grounded: dict[str, GroundedVectorEstimate]
    calibration_gaps: dict[str, float]
    grounded_coverage: float
    overall_calibration_score: float
    phase: str = "combined"
    insufficient_evidence_vectors: list[str] = None  # type: ignore[assignment]
    calibration_status: str = "grounded"
    # A3 additions — optional with defaults for backward compatibility
    grounded_rationale: str | None = None
    criticality: str | None = None
    parent_transaction_id: str | None = None

    def __post_init__(self):
        if self.insufficient_evidence_vectors is None:
            self.insufficient_evidence_vectors = []

    @property
    def observed(self) -> dict[str, GroundedVectorEstimate]:
        """Alias for 'grounded' — the SPEC 1 name for service-computed vectors.

        The field is named 'grounded' for backward compatibility. When B3
        wires the AI-reasoned grounded state, the distinction becomes:
        - self.observed → what the services measured
        - self.grounded → what the AI reasoned (not yet implemented)
        For now they are the same object.
        """
        return self.grounded


def _load_domain_weights(domain: str = "default", work_type: str | None = None) -> dict[str, Any]:
    """Load category weights and vector-category map from confidence_weights.yaml.

    Resolution priority (the triad): work_type > domain > default.
    When work_type is known, its category weights override domain weights.
    This makes calibration scoring sensitive to what kind of work is being done,
    not just which project/domain it's in.

    Args:
        domain: Domain name (software, consulting, research, operations, default)
        work_type: Work type from PREFLIGHT (code, research, debug, docs, etc.)

    Returns:
        Dict with 'category_weights', 'vector_category_map', and 'weight_source'
    """
    config_path = Path(__file__).parent.parent.parent / "config" / "mco" / "confidence_weights.yaml"
    defaults = {
        # Categories: foundation, comprehension, execution, meta.
        # Renamed from "engagement" → "meta" on 2026-04-07 — the 4th
        # category contains both engagement AND uncertainty (relational
        # vectors), so "meta" is the truth-aligned name.
        "category_weights": {"foundation": 0.35, "comprehension": 0.25, "execution": 0.25, "meta": 0.15},
        "vector_category_map": {
            "know": "foundation",
            "do": "foundation",
            "context": "foundation",
            "clarity": "comprehension",
            "coherence": "comprehension",
            "signal": "comprehension",
            "density": "comprehension",
            "state": "execution",
            "change": "execution",
            "completion": "execution",
            "impact": "execution",
            "engagement": "meta",
            "uncertainty": "meta",
        },
        "weight_source": "default",
    }
    if not config_path.exists():
        return defaults

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

        vector_map = config.get("vector_category_map", defaults["vector_category_map"])

        # Priority: work_type > domain > default
        weight_source = "default"
        category_weights = defaults["category_weights"]

        # Try work_type first (most specific)
        if work_type:
            wt_weights = config.get("work_type_category_weights", {})
            if work_type in wt_weights:
                category_weights = wt_weights[work_type]
                weight_source = f"work_type:{work_type}"

        # Fall back to domain if work_type didn't match
        if weight_source == "default":
            domain_weights = config.get("domain_category_weights", {})
            if domain in domain_weights:
                category_weights = domain_weights[domain]
                weight_source = f"domain:{domain}"
            elif "default" in domain_weights:
                category_weights = domain_weights["default"]
                weight_source = "domain:default"

        return {
            "category_weights": category_weights,
            "vector_category_map": vector_map,
            "weight_source": weight_source,
        }
    except Exception as e:
        logger.warning(f"Failed to load domain weights: {e}")
        return defaults


def _compute_meta_uncertainty(
    grounded: "dict[str, GroundedVectorEstimate]",
    calibration_gaps: dict[str, float],
    grounded_coverage: float,
) -> float | None:
    """Compute the grounded uncertainty observation as a META quantity.

    Uncertainty = "how confident is the AI in the other 12 vectors it just
    reported?" — derived from the coverage (how much we could verify) and
    gap magnitudes (how wrong the verified parts were) of the OTHER vectors.

    NOTE: This value is computed for FEEDBACK and REPORTING only. It appears
    in calibration_gaps so the AI can see the divergence, and it gates CHECK
    via the uncertainty threshold. However, it is EXCLUDED from the calibration
    score computation because its grounded value is derived from the same gaps
    it would be scored against — a circular dependency that adds noise, not signal.

    This aligns with the statusline's confidence formula, which treats
    confidence as a derived display quantity computed from know/context/
    completion/(1-uncertainty). The vector layer follows the same pattern:
    uncertainty is computed from the other grounded vectors, not measured
    directly from domain-specific artifact counts.

    Semantic:
        0.0 = fully certain (self-assessment is well-calibrated, coverage high)
        1.0 = maximally uncertain (either low coverage or large gaps)
        0.5 = neutral default when there's no signal to judge against

    Formula:
        coverage_u = 1.0 - grounded_coverage
            High coverage = we could verify a lot = low uncertainty contribution
            Low coverage = we couldn't verify much = high uncertainty contribution
        gap_u = clamp(mean(|gap|) * 2.0, 0, 1)
            Scaled so a mean gap of 0.5 = max uncertainty contribution.
            Uses absolute gaps to treat over- and under-estimation equally.
        uncertainty = 0.4 * coverage_u + 0.6 * gap_u
            Gaps weighted more heavily than coverage — actually being wrong
            is a stronger uncertainty signal than having less evidence.

    Args:
        grounded: Dict of grounded vector estimates (excluding UNGROUNDABLE_VECTORS
                 and META_VECTORS).
        calibration_gaps: Self - grounded gaps for the grounded vectors.
        grounded_coverage: Fraction of self-assessed vectors that had evidence.

    Returns:
        Float in [0.0, 1.0], or None if there's nothing to compute from
        (no grounded vectors → insufficient evidence for meta-uncertainty).
    """
    # Use gaps from OTHER vectors (exclude meta vectors to avoid circularity)
    other_gaps = [
        abs(g) for v, g in calibration_gaps.items() if v not in META_VECTORS and v not in UNGROUNDABLE_VECTORS
    ]

    # If we have neither coverage signal nor gap signal, return insufficient.
    if not other_gaps and grounded_coverage == 0:
        return None

    # Coverage term: missing evidence → uncertainty
    coverage_uncertainty = max(0.0, 1.0 - grounded_coverage)

    # Gap term: wrong predictions → uncertainty.
    # Scale so mean gap of 0.5 = max uncertainty contribution.
    if other_gaps:
        mean_gap = sum(other_gaps) / len(other_gaps)
        gap_uncertainty = min(1.0, mean_gap * 2.0)
    else:
        # Coverage exists but no gap data — use coverage alone
        gap_uncertainty = 0.0

    # Weighted combination: gaps matter more than coverage absence
    meta = 0.4 * coverage_uncertainty + 0.6 * gap_uncertainty
    return round(max(0.0, min(1.0, meta)), 4)


def _compute_weighted_calibration(
    calibration_gaps: dict[str, float],
    domain: str = "default",
    per_vector_weights: dict[str, float] | None = None,
    work_type: str | None = None,
) -> float:
    """Compute category-weighted calibration score.

    Tier 1: Work-type (or domain) category weights determine how much each category contributes.
    Tier 2: Per-vector weights (optional) scale individual vector gaps within categories.

    Resolution: work_type > domain > default (the triad).

    Args:
        calibration_gaps: Dict of vector_name → gap (self - grounded)
        domain: Domain for Tier 1 category weights
        per_vector_weights: Optional Tier 2 per-vector weights (from project.yaml)

    Returns:
        Weighted calibration score (lower = better calibrated)
    """
    if not calibration_gaps:
        return 0.0

    config = _load_domain_weights(domain, work_type=work_type)
    category_weights = config["category_weights"]
    vector_map = config["vector_category_map"]

    # Group gaps by category
    category_gaps: dict[str, list[tuple[str, float]]] = {}
    for vector_name, gap in calibration_gaps.items():
        category = vector_map.get(vector_name, "foundation")
        if category not in category_gaps:
            category_gaps[category] = []

        # Apply per-vector weight (Tier 2) if provided
        vector_weight = 1.0
        if per_vector_weights:
            vector_weight = per_vector_weights.get(vector_name, 1.0)

        category_gaps[category].append((vector_name, abs(gap) * vector_weight))

    # Weighted mean: category weight * mean abs gap within category
    total_score = 0.0
    total_weight = 0.0
    for category, gaps in category_gaps.items():
        cat_weight = category_weights.get(category, 0.25)
        cat_mean = sum(g for _, g in gaps) / len(gaps)
        total_score += cat_weight * cat_mean
        total_weight += cat_weight

    # Normalize by actual weight used (not all categories may have grounded vectors)
    if total_weight > 0:
        return total_score / total_weight
    return 0.0


def summarize_evidence(bundle: EvidenceBundle, work_type: str | None = None) -> dict[str, Any]:
    """Summarize evidence as structured data for AI interpretation.

    Instead of mapping evidence to per-vector scores, presents raw
    evidence grouped by source. The AI interprets this to calibrate
    its own vectors — it's the calibrator, not the system.
    """
    relevance = WORK_TYPE_RELEVANCE.get(work_type, {}) if work_type else {}

    summary: dict[str, Any] = {}
    for item in bundle.items:
        source_relevance = relevance.get(item.source, 1.0)
        if source_relevance <= 0.0:
            continue  # instrument-blind for this work_type

        if item.source not in summary:
            summary[item.source] = {}
        summary[item.source][item.metric_name] = item.raw_value

    # Generate natural-language signals from evidence patterns
    signals = _generate_evidence_signals(summary, bundle)

    return {
        "sources": summary,
        "signals": signals,
        "coverage": round(bundle.coverage, 2),
        "evidence_count": len(bundle.items),
    }


#: Evidence-item keys inside the ``artifacts`` source that carry a per-type count,
#: mapped to (item_key, count_key). The collector emits NO metric for assumptions
#: or decisions, so this signal is structurally blind to two of the six types — see
#: the docstring for why that must change what it says rather than what it guesses.
_BREADTH_OBSERVABLE = {
    "findings": ("mistake_ratio", "findings"),
    "mistakes": ("mistake_ratio", "mistakes"),
    "dead_ends": ("productive_exploration_ratio", "dead_ends_weighted"),
    "unknowns": ("unknown_resolution_ratio", "total_weighted"),
}


def _signal_artifact_breadth(summary: dict) -> str | None:
    """Signal for artifact logging breadth.

    Rewritten 2026-07-31 — the previous version fired on EVERY transaction and
    could never emit its own positive branch.

    ``summary["artifacts"]`` is the evidence-SOURCE dict: its values are per-metric
    sub-dicts, not numbers. The old filter was
    ``[k for k, v in artifacts.items() if isinstance(v, (int, float)) and v > 0]``,
    which therefore matched nothing, always — so ``artifact_types`` was always
    empty, ``<= 1`` was always true, the narrow warning fired unconditionally, and
    ``Good artifact breadth`` was unreachable code. Verified by executing the
    function against a live payload: a transaction logging findings, mistakes,
    assumptions AND decisions still got "Narrow artifact breadth".

    That is the failure cortex's SER item 4 reported as "the retrospective gate
    repeats identically and I learned to discount it". It was not repetition —
    the predicate could not be satisfied. **A check that cannot pass is training,
    not feedback**, and what it trains is dismissal of every signal beside it.

    Two constraints on the fix:

    1. *Unmeasurable must not read as narrow.* When no per-type counts are
       observable at all, return ``None`` — silence — rather than asserting a
       deficiency that was never measured.
    2. *Do not recommend what cannot be verified.* The old text advised logging
       "decisions, assumptions, or dead-ends" while the collector emits no metric
       for decisions or assumptions, so complying could not silence it. The new
       text names only observable types, and says how many it could see, so a
       reader can tell a real narrow result from a partial view.
    """
    artifacts = summary.get("artifacts", {})
    if not artifacts:
        return None

    present = []
    for label, (item_key, count_key) in _BREADTH_OBSERVABLE.items():
        item = artifacts.get(item_key)
        if not isinstance(item, dict):
            continue
        value = item.get(count_key)
        if isinstance(value, (int, float)) and value > 0:
            present.append(label)

    observable = sum(1 for item_key, _ in _BREADTH_OBSERVABLE.values() if isinstance(artifacts.get(item_key), dict))
    if observable == 0:
        # Nothing to measure. Saying nothing beats inventing a verdict.
        return None

    if len(present) <= 1:
        seen = present[0] if present else "none"
        return (
            f"Narrow artifact breadth — only {seen} logged of the "
            f"{observable} type(s) measurable here; consider dead-ends or mistakes "
            "(assumptions/decisions are not observable in this evidence set)"
        )
    if len(present) >= 3:
        return f"Good artifact breadth ({len(present)} observable types)"
    return None


def _signal_git_activity(summary: dict) -> str | None:
    """Signal for git commit activity."""
    git = summary.get("git", {})
    if not git:
        return None
    commit_data = git.get("commit_count", 0)
    # raw_value is a dict like {"commits": N, "maturity": "..."} — extract the int
    commits = commit_data.get("commits", 0) if isinstance(commit_data, dict) else commit_data
    if commits == 0:
        return "No commits in this transaction — uncommitted work is invisible to calibration"
    if commits >= 3:
        return f"Active execution ({commits} commits)"
    return None


def _extract_int(data, key: str, default: int = 0) -> int:
    """Safely extract an int from evidence data (which may be a dict raw_value)."""
    if isinstance(data, dict):
        value = data.get(key, default) if key in data else default
        return int(value) if isinstance(value, (int, float)) else default
    return int(data) if isinstance(data, (int, float)) else default


def _signal_code_quality(summary: dict) -> str | None:
    """Signal for code quality metrics."""
    cq = summary.get("code_quality", {})
    if not cq:
        return None
    ruff_data = cq.get("ruff_violation_density", cq.get("ruff_violation_count"))
    pyright_data = cq.get("pyright_type_safety", cq.get("pyright_error_count"))
    ruff_violations = _extract_int(ruff_data, "violations") if ruff_data else None
    pyright_errors = _extract_int(pyright_data, "errors") if pyright_data else None
    if ruff_violations == 0 and pyright_errors == 0:
        return "Code quality clean (0 lint, 0 type errors)"
    if ruff_violations is not None and ruff_violations > 0:
        return f"Lint regressions: {ruff_violations} ruff violations"
    return None


def _signal_test_results(summary: dict) -> str | None:
    """Signal for pytest results."""
    tests = summary.get("pytest", {})
    if not tests:
        return None
    failed = tests.get("failed_count", 0)
    if failed > 0:
        return f"Test regressions: {failed} tests failing"
    passed = tests.get("passed_count", 0)
    if passed > 0:
        return f"Tests passing ({passed})"
    return None


def _signal_noetic_activity(summary: dict) -> str | None:
    """Signal for investigation depth."""
    noetic = summary.get("noetic", {})
    if not noetic:
        return None
    reads = noetic.get("file_reads", 0)
    searches = noetic.get("searches", 0)
    if reads + searches > 20:
        return f"Deep investigation ({reads} reads, {searches} searches)"
    return None


def _signal_sentinel(summary: dict) -> str | None:
    """Signal for sentinel blocks."""
    sentinel = summary.get("sentinel", {})
    if not sentinel:
        return None
    blocks = sentinel.get("blocks", 0)
    if blocks > 0:
        return f"Sentinel blocked {blocks} premature praxic attempts"
    return None


# Ordered list of signal generators applied to the evidence summary.
_SIGNAL_GENERATORS = [
    _signal_artifact_breadth,
    _signal_git_activity,
    _signal_code_quality,
    _signal_test_results,
    _signal_noetic_activity,
    _signal_sentinel,
]


def _generate_evidence_signals(summary: dict, bundle: EvidenceBundle) -> list[str]:
    """Generate natural-language signals from evidence patterns."""
    signals = []
    for generator in _SIGNAL_GENERATORS:
        signal = generator(summary)
        if signal is not None:
            signals.append(signal)
    return signals


class EvidenceMapper:
    """Maps evidence bundles to grounded vector estimates."""

    def map_evidence(
        self,
        bundle: EvidenceBundle,
        self_assessed_vectors: dict[str, float],
        phase: str = "combined",
        domain: str = "default",
        per_vector_weights: dict[str, float] | None = None,
        work_type: str | None = None,
    ) -> GroundedAssessment:
        """Map evidence to grounded vector estimates and compare to self-assessment.

        Work-type-aware:
          - Sources with relevance == 0.0 are EXCLUDED for this work_type
            (instrument-blind: cannot sample the kind of work being done).
          - Sources with relevance > 0.0 contribute with that multiplier.
          - Vectors that ONLY had evidence from excluded sources are marked
            insufficient_evidence — no grounded value is computed, no gap
            is recorded, no false drift is written to the trajectory.
            The AI's self-assessment stands as the best available estimate.
        """
        # Work-type relevance profile (scales evidence weights by source relevance)
        relevance = WORK_TYPE_RELEVANCE.get(work_type, {}) if work_type else {}

        # Track which vectors have excluded-only evidence (instrument-blind for this work_type).
        # Vectors here saw evidence from at least one source, but every source was excluded.
        vectors_seen_in_excluded: set[str] = set()
        # Vectors here saw evidence from at least one INCLUDED source.
        vectors_with_included: set[str] = set()

        # Group evidence by supported vector, applying exclusion semantic
        vector_evidence: dict[str, list[tuple[EvidenceItem, float]]] = {}
        for item in bundle.items:
            quality_weight = QUALITY_WEIGHTS.get(item.quality, 0.5)
            # Apply work-type relevance scaling to evidence source.
            # 0.0 = explicitly excluded (instrument-blind for this work_type).
            # Missing source defaults to 1.0 (neutral).
            source_relevance = relevance.get(item.source, 1.0)
            if source_relevance <= 0.0:
                # Source is instrument-blind for this work_type. Skip it
                # entirely so it doesn't pull grounded estimates toward
                # absent-signal values. Record which vectors it would have
                # contributed to so we can detect "all-excluded" vectors.
                for vector in item.supports_vectors:
                    if vector not in UNGROUNDABLE_VECTORS:
                        vectors_seen_in_excluded.add(vector)
                continue

            weight = quality_weight * source_relevance
            for vector in item.supports_vectors:
                if vector not in vector_evidence:
                    vector_evidence[vector] = []
                vector_evidence[vector].append((item, weight))
                vectors_with_included.add(vector)

        # Compute grounded estimates via weighted average
        grounded = {}
        for vector_name, evidence_list in vector_evidence.items():
            if vector_name in UNGROUNDABLE_VECTORS:
                continue

            total_weight = sum(w for _, w in evidence_list)
            if total_weight == 0:
                continue

            weighted_value = sum(item.value * w for item, w in evidence_list) / total_weight
            primary_source = max(evidence_list, key=lambda x: x[1])[0].source

            grounded[vector_name] = GroundedVectorEstimate(
                vector_name=vector_name,
                estimated_value=max(0.0, min(1.0, weighted_value)),
                confidence=min(1.0, total_weight / len(evidence_list)),
                evidence_count=len(evidence_list),
                primary_source=primary_source,
            )

        # Compute insufficient_evidence_vectors: vectors that had evidence
        # ONLY from excluded sources (no included evidence remained after filter).
        insufficient = sorted(vectors_seen_in_excluded - vectors_with_included)

        # Compute calibration gaps (self - grounded)
        # Positive = AI overestimates, Negative = AI underestimates.
        # Insufficient-evidence vectors are NOT included — no fabricated drift.
        calibration_gaps = {}
        for vector_name, estimate in grounded.items():
            self_val = self_assessed_vectors.get(vector_name, 0.5)
            calibration_gaps[vector_name] = round(self_val - estimate.estimated_value, 4)

        # Meta-uncertainty computation: uncertainty is derived from the
        # OTHER 12 vectors' coverage and gap magnitudes, not from direct
        # measurement of first-order doubt proxies. See statusline's
        # confidence formula — this keeps the vector layer aligned with
        # how the UI already presents calibration state.
        #
        # Only computed when self_assessed has uncertainty (so we can
        # compare) AND there are other grounded vectors to judge against.
        if "uncertainty" in self_assessed_vectors and "uncertainty" not in grounded:
            meta_u = _compute_meta_uncertainty(grounded, calibration_gaps, bundle.coverage)
            if meta_u is not None:
                grounded["uncertainty"] = GroundedVectorEstimate(
                    vector_name="uncertainty",
                    estimated_value=meta_u,
                    confidence=0.8,  # meta-derived, moderate confidence
                    evidence_count=len(calibration_gaps),  # gaps from other vectors
                    primary_source="meta",
                )
                self_u = self_assessed_vectors["uncertainty"]
                calibration_gaps["uncertainty"] = round(self_u - meta_u, 4)

        # Overall calibration score — work-type/domain-weighted (Tier 1 + optional Tier 2)
        # Exclude uncertainty from scoring: it's a meta-vector whose "grounded"
        # value is derived from the same gaps it would be scored against (circular).
        # Uncertainty still appears in calibration_gaps for feedback/reporting
        # and still gates CHECK — it's just not part of the Brier score.
        scoring_gaps = {k: v for k, v in calibration_gaps.items() if k != "uncertainty"}
        overall_score = _compute_weighted_calibration(
            scoring_gaps,
            domain=domain,
            per_vector_weights=per_vector_weights,
            work_type=work_type,
        )

        return GroundedAssessment(
            session_id=bundle.session_id,
            self_assessed=self_assessed_vectors,
            grounded=grounded,
            calibration_gaps=calibration_gaps,
            grounded_coverage=bundle.coverage,
            overall_calibration_score=round(overall_score, 4),
            phase=phase,
            insufficient_evidence_vectors=insufficient,
        )

"""
Dynamic Thresholds — Brier Score Based Calibration Gating

Computes phase-aware CHECK gate thresholds using Brier score (a strictly proper
scoring rule) instead of MAE (improper — incentivizes extreme predictions).

Threshold model:
- Good calibration (low Brier reliability) → threshold stays at domain baseline
- Bad calibration (high Brier reliability) → threshold RAISED to compensate for bias
- Thresholds never go BELOW domain baselines — good calibration is not rewarded
  with a lower bar, it's rewarded with the system trusting the numbers as-is

Brier decomposition (Murphy 1973):
  BS = Reliability - Resolution + Uncertainty
  - Reliability: calibration error (lower = better calibrated, 0 = perfect)
  - Resolution: discrimination power (higher = better at distinguishing easy/hard)
  - Uncertainty: inherent domain difficulty (not controllable)

Only Reliability drives threshold inflation. Resolution and Uncertainty are diagnostic.

Self-correcting properties:
- Overconfidence → high reliability component → tighter gates → forced investigation
- Good calibration → reliability near 0 → gates stay at baseline → numbers trusted
- Phase-specific → noetic and praxic competence are independent axes

References:
- Brier (1950): "Verification of forecasts expressed in terms of probability"
- Murphy (1973): Brier score decomposition into reliability + resolution - uncertainty
- Sahoo et al. (NeurIPS 2021): "Reliable Decisions with Threshold Calibration"
- Gneiting & Raftery (2007): "Strictly Proper Scoring Rules, Prediction, and Estimation"
"""

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Hardcoded fallbacks — used only when MCO config unavailable
_FALLBACK_BASELINES = {
    "ready_know_threshold": 0.70,
    "ready_uncertainty_threshold": 0.35,
}
_FALLBACK_CEILINGS = {
    "ready_know_threshold": 0.90,
    "ready_uncertainty_threshold": 0.15,
}
_FALLBACK_MAX_INFLATION = 0.05
_FALLBACK_MIN_TRANSACTIONS = 5
_FALLBACK_LOOKBACK = 20


def _load_calibration_config() -> dict:
    """Load calibration gating config from MCO cascade_styles.yaml via ThresholdLoader.

    Returns dict with keys: baselines, ceilings, max_inflation, min_transactions, lookback.
    Falls back to hardcoded values if MCO config unavailable.
    """
    try:
        from empirica.config.threshold_loader import ThresholdLoader

        loader = ThresholdLoader.get_instance()
        return {
            "baselines": {
                "ready_know_threshold": loader.get(
                    "cascade.ready_know_threshold",
                    _FALLBACK_BASELINES["ready_know_threshold"],
                ),
                "ready_uncertainty_threshold": loader.get(
                    "cascade.ready_uncertainty_threshold",
                    _FALLBACK_BASELINES["ready_uncertainty_threshold"],
                ),
            },
            "ceilings": {
                "ready_know_threshold": loader.get(
                    "calibration.safety_ceiling_know",
                    _FALLBACK_CEILINGS["ready_know_threshold"],
                ),
                "ready_uncertainty_threshold": loader.get(
                    "calibration.safety_ceiling_uncertainty",
                    _FALLBACK_CEILINGS["ready_uncertainty_threshold"],
                ),
            },
            "max_inflation": loader.get(
                "calibration.max_inflation",
                _FALLBACK_MAX_INFLATION,
            ),
            "min_transactions": loader.get(
                "calibration.min_transactions",
                _FALLBACK_MIN_TRANSACTIONS,
            ),
            "lookback": loader.get(
                "calibration.lookback",
                _FALLBACK_LOOKBACK,
            ),
        }
    except Exception:
        logger.debug("MCO config unavailable, using fallback calibration values")
        return {
            "baselines": _FALLBACK_BASELINES,
            "ceilings": _FALLBACK_CEILINGS,
            "max_inflation": _FALLBACK_MAX_INFLATION,
            "min_transactions": _FALLBACK_MIN_TRANSACTIONS,
            "lookback": _FALLBACK_LOOKBACK,
        }


@dataclass
class BrierDecomposition:
    """Murphy (1973) decomposition of Brier score."""

    brier_score: float  # Overall: 0 = perfect, 1 = worst
    reliability: float  # Calibration error: 0 = perfectly calibrated
    resolution: float  # Discrimination: higher = better at distinguishing
    uncertainty: float  # Domain difficulty: not controllable
    n_predictions: int  # Sample size
    n_bins: int  # Bins used for decomposition


def compute_brier_score(predictions: list[tuple[float, float]]) -> float:
    """Compute raw Brier score from (predicted, observed) pairs.

    Args:
        predictions: List of (predicted_probability, observed_outcome) tuples.
                    predicted is 0.0-1.0, observed is the grounded value 0.0-1.0.

    Returns:
        Brier score (0.0 = perfect, 1.0 = worst).
    """
    if not predictions:
        return 1.0  # Worst possible — no data
    return sum((p - o) ** 2 for p, o in predictions) / len(predictions)


def compute_check_brier(
    check_results: list[dict],
) -> dict | None:
    """Compute Brier score from compliance check predictions vs actuals (B4).

    This is the falsifiable Brier path: the AI predicts P(check passes),
    the check runs, the outcome is ground truth. Unlike vector-divergence
    Brier, this measures a real falsifiable prediction.

    Args:
        check_results: List of dicts with 'passed' (bool) and optional
                      'predicted_pass' (float 0-1). Only checks with
                      predictions contribute to the score.

    Returns:
        Dict with brier_score, n_predictions, per_check breakdown,
        or None if no predictions were made.
    """
    pairs = []
    per_check = []
    for cr in check_results:
        predicted = cr.get("predicted_pass")
        if predicted is None:
            continue
        # Exclude deferred (not run) and cached (stale prediction surface)
        if cr.get("deferred"):
            continue
        actual = 1.0 if cr.get("passed") else 0.0
        contribution = (predicted - actual) ** 2
        pairs.append((predicted, actual))
        per_check.append(
            {
                "check_id": cr.get("check_id", "unknown"),
                "predicted_pass": predicted,
                "actual_pass": actual == 1.0,
                "brier_contribution": round(contribution, 4),
            }
        )

    if not pairs:
        return None

    brier = sum((p - o) ** 2 for p, o in pairs) / len(pairs)
    return {
        "brier_score": round(brier, 4),
        "n_predictions": len(pairs),
        "per_check": per_check,
        "interpretation": (
            "perfect" if brier < 0.01 else "good" if brier < 0.1 else "moderate" if brier < 0.25 else "poor"
        ),
    }


def compute_brier_decomposition(
    predictions: list[tuple[float, float]],
    n_bins: int = 10,
) -> BrierDecomposition:
    """Compute Murphy (1973) Brier score decomposition.

    Decomposes into: BS = Reliability - Resolution + Uncertainty

    Args:
        predictions: List of (predicted_probability, observed_outcome) tuples.
        n_bins: Number of bins for decomposition (default 10).

    Returns:
        BrierDecomposition with reliability, resolution, uncertainty components.
    """
    if not predictions:
        return BrierDecomposition(
            brier_score=1.0,
            reliability=1.0,
            resolution=0.0,
            uncertainty=0.25,
            n_predictions=0,
            n_bins=0,
        )

    n = len(predictions)

    # Overall observed mean (base rate)
    o_bar = sum(o for _, o in predictions) / n

    # Uncertainty: variance of outcomes (inherent difficulty)
    # For continuous outcomes, use sample variance
    uncertainty = sum((o - o_bar) ** 2 for _, o in predictions) / n

    # Bin predictions by predicted probability
    bins: dict[int, list[tuple[float, float]]] = {}
    for pred, obs in predictions:
        bin_idx = min(int(pred * n_bins), n_bins - 1)
        bins.setdefault(bin_idx, []).append((pred, obs))

    # Reliability: weighted average of (forecast_mean - observed_mean)^2 per bin
    reliability = 0.0
    resolution = 0.0
    for _bin_idx, bin_items in bins.items():
        n_k = len(bin_items)
        f_k = sum(p for p, _ in bin_items) / n_k  # Mean forecast in bin
        o_k = sum(o for _, o in bin_items) / n_k  # Mean observed in bin

        reliability += (n_k / n) * (f_k - o_k) ** 2
        resolution += (n_k / n) * (o_k - o_bar) ** 2

    brier_score = reliability - resolution + uncertainty

    return BrierDecomposition(
        brier_score=round(max(0.0, brier_score), 6),
        reliability=round(reliability, 6),
        resolution=round(resolution, 6),
        uncertainty=round(uncertainty, 6),
        n_predictions=n,
        n_bins=len(bins),
    )


def compute_dynamic_thresholds(
    ai_id: str,
    db,
    base_thresholds: dict | None = None,
    min_transactions: int | None = None,
    lookback: int | None = None,
) -> dict:
    """Compute phase-aware dynamic thresholds using Brier score reliability.

    Threshold model:
    - reliability near 0 → well calibrated → thresholds stay at domain baseline
    - reliability > 0 → miscalibrated → thresholds inflated proportionally
    - inflation = min(reliability * scale_factor, max_inflation)

    All config values (baselines, ceilings, max_inflation, min_transactions, lookback)
    are read from MCO cascade_styles.yaml via ThresholdLoader. Explicit args override config.

    Args:
        ai_id: AI identifier (e.g., "claude-code")
        db: Database connection
        base_thresholds: Override domain baselines (default: from MCO config)
        min_transactions: Override minimum trajectory points (default: from MCO config)
        lookback: Override lookback window (default: from MCO config)

    Returns:
        {
            "noetic": {
                "ready_know_threshold": float,
                "ready_uncertainty_threshold": float,
                "brier_score": float,
                "brier_reliability": float,   # Drives threshold inflation
                "brier_resolution": float,    # Diagnostic only
                "brier_uncertainty": float,   # Diagnostic only
                "threshold_inflation": float, # How much thresholds were raised
                "transactions_analyzed": int,
            },
            "praxic": { ... same ... },
            "source": "dynamic" | "static",
            "reason": str,
        }
    """
    # Load all config from MCO (falls back to hardcoded if unavailable)
    config = _load_calibration_config()

    base = base_thresholds or config["baselines"]
    know_base = base.get("ready_know_threshold", 0.70)
    unc_base = base.get("ready_uncertainty_threshold", 0.35)
    min_txns = min_transactions if min_transactions is not None else config["min_transactions"]
    lb = lookback if lookback is not None else config["lookback"]
    max_infl = config["max_inflation"]
    ceilings = config["ceilings"]

    static_phase = {
        "ready_know_threshold": know_base,
        "ready_uncertainty_threshold": unc_base,
        "brier_score": None,
        "brier_reliability": None,
        "brier_resolution": None,
        "brier_uncertainty": None,
        "threshold_inflation": 0.0,
        "transactions_analyzed": 0,
    }
    static_result = {
        "noetic": {**static_phase},
        "praxic": {**static_phase},
        "source": "static",
        "reason": "insufficient data",
    }

    try:
        cursor = db.conn.cursor()
        result: dict[str, Any] = {"source": "dynamic", "reason": "brier calibration"}

        for phase in ["noetic", "praxic"]:
            # Get recent trajectory points with both self-assessed and grounded
            cursor.execute(
                """
                SELECT self_assessed, grounded
                FROM calibration_trajectory
                WHERE ai_id = ? AND phase = ? AND grounded IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                (ai_id, phase, lb),
            )

            rows = cursor.fetchall()

            if len(rows) < min_txns:
                result[phase] = {**static_phase, "transactions_analyzed": len(rows)}
                continue

            # Build prediction pairs: (self_assessed, grounded)
            predictions = [(row[0], row[1]) for row in rows]

            # Compute Brier decomposition
            decomp = compute_brier_decomposition(predictions)

            # Threshold inflation driven by RELIABILITY component only
            # reliability = 0 → no inflation (well calibrated)
            # reliability > 0 → inflate proportionally, capped at max_inflation
            # Design: inflation is INFORMATIVE, not punitive. Small increments
            # (max 0.05) are enough for the AI to notice calibration cost.
            # The AI makes the holistic judgment — this is a measurement system,
            # not a rules-based gate. Max threshold = base + 0.05 = 0.75.
            raw_inflation = min(decomp.reliability * (max_infl / 0.15), max_infl)

            # Cold-start damper: with few data points, reliability estimates are
            # noisy. Scale inflation by confidence in the estimate to prevent the
            # "death spiral" where high inflation from noisy data blocks CHECK,
            # preventing new data from being collected to correct the estimate.
            # Ramp: 5 txns = 25% confidence, 10 = 50%, 20+ = 100%
            confidence = min(1.0, decomp.n_predictions / 20.0)
            inflation = raw_inflation * confidence

            # Apply inflation: raise know threshold, lower uncertainty tolerance
            know_adjusted = know_base + inflation
            unc_adjusted = unc_base - inflation

            # Clamp to safety ceilings (from MCO config)
            know_adjusted = min(ceilings["ready_know_threshold"], know_adjusted)
            unc_adjusted = max(ceilings["ready_uncertainty_threshold"], unc_adjusted)

            result[phase] = {
                "ready_know_threshold": round(know_adjusted, 3),
                "ready_uncertainty_threshold": round(unc_adjusted, 3),
                "brier_score": decomp.brier_score,
                "brier_reliability": decomp.reliability,
                "brier_resolution": decomp.resolution,
                "brier_uncertainty": decomp.uncertainty,
                "threshold_inflation": round(inflation, 3),
                "transactions_analyzed": decomp.n_predictions,
            }

        # If both phases are still static, mark overall as static
        noetic_static = result.get("noetic", {}).get("brier_score") is None
        praxic_static = result.get("praxic", {}).get("brier_score") is None
        if noetic_static and praxic_static:
            return static_result

        return result

    except Exception as e:
        logger.debug(f"Dynamic threshold computation failed (non-fatal): {e}")
        return static_result


def get_brier_profile(
    ai_id: str,
    db,
    lookback: int = 50,
    session_id: str | None = None,
) -> dict:
    """Get Brier score profile for an AI across phases.

    Useful for calibration-report and epistemic profiles.
    Returns decomposition per phase with trend information.

    ``session_id`` scopes to a single PRACTITIONER (claude session) rather than
    the practice aggregate (B5): every trajectory point already carries
    ``session_id``, so passing it filters the same decomposition to one
    practitioner's own calibration track. Default ``None`` = the practice
    aggregate (``WHERE ai_id = ?``), unchanged for existing callers.
    """
    try:
        cursor = db.conn.cursor()
        profile = {}

        for phase in ["noetic", "praxic", "combined"]:
            phase_filter = "AND phase = ?" if phase != "combined" else ""
            session_filter = "AND session_id = ?" if session_id else ""
            params = (
                [ai_id] + ([phase] if phase != "combined" else []) + ([session_id] if session_id else []) + [lookback]
            )

            cursor.execute(
                f"""
                SELECT self_assessed, grounded
                FROM calibration_trajectory
                WHERE ai_id = ? {phase_filter} {session_filter} AND grounded IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT ?
            """,
                params,
            )

            rows = cursor.fetchall()
            if len(rows) < 3:
                profile[phase] = {"status": "insufficient_data", "n": len(rows)}
                continue

            predictions = [(row[0], row[1]) for row in rows]
            decomp = compute_brier_decomposition(predictions)

            # Per-sample Brier components (the same (p−o)² the score averages) —
            # expose their SAMPLE variance (ddof=1) so a consumer can form the
            # true SE = sqrt(brier_variance / n) rather than the conservative
            # bound SE ≤ 1/(2·√n) that assumes max dispersion (var=0.25 for a
            # [0,1] component). Raw dispersion stat, not a weighting — the
            # shrinkage fold stays autonomy's lane (PRACTITIONER_DELIBERATION_MODEL
            # §3, B7). n ≥ 3 here (guarded above), so the ddof=1 denominator ≥ 2.
            components = [(p - o) ** 2 for p, o in predictions]
            mean_component = sum(components) / len(components)
            brier_variance = sum((c - mean_component) ** 2 for c in components) / (len(components) - 1)

            # Compute recent vs historical for trend
            recent = predictions[: len(predictions) // 2] if len(predictions) >= 6 else predictions
            historical = predictions[len(predictions) // 2 :] if len(predictions) >= 6 else []

            trend = "stable"
            if historical:
                recent_brier = compute_brier_score(recent)
                historical_brier = compute_brier_score(historical)
                diff = recent_brier - historical_brier
                if diff < -0.02:
                    trend = "improving"
                elif diff > 0.02:
                    trend = "degrading"

            profile[phase] = {
                "brier_score": decomp.brier_score,
                "brier_variance": round(brier_variance, 6),
                "reliability": decomp.reliability,
                "resolution": decomp.resolution,
                "uncertainty": decomp.uncertainty,
                "n_predictions": decomp.n_predictions,
                "trend": trend,
            }

        return profile

    except Exception as e:
        logger.debug(f"Brier profile computation failed: {e}")
        return {}


def get_practitioner_brier_profile(
    session_id: str,
    ai_id: str,
    db,
    lookback: int = 50,
) -> dict:
    """Per-PRACTITIONER Brier profile (EPIC B / B5) — the session-keyed
    reliability track that's already latent in ``calibration_trajectory``.

    Same phase decomposition as :func:`get_brier_profile`, scoped to one
    practitioner (claude session) via the ``session_id`` every trajectory point
    already carries — so a practitioner's OWN calibration surfaces rather than
    being rolled up to the practice aggregate. This is the RAW surface; the
    credibility-weighted fold (Bühlmann shrinkage on thin ``n``) is autonomy's
    lane, applied at arbitration time (PRACTITIONER_DELIBERATION_MODEL §3).
    """
    return get_brier_profile(ai_id, db, lookback=lookback, session_id=session_id)


def compute_practitioner_divergence(
    session_id: str,
    ai_id: str,
    db,
    lookback: int = 50,
) -> dict:
    """Per-phase practitioner-vs-practice reliability divergence (EPIC B / B5).

    Returns, per phase, the RAW divergence between a practitioner's own Brier
    track and the practice aggregate::

        {phase: {brier_delta, reliability_delta, practitioner_brier,
                 practice_brier, practitioner_n, practice_n,
                 practitioner_brier_variance, practice_brier_variance}}

    The per-side ``brier_variance`` (sample variance, ddof=1, of the per-sample
    Brier components) lets a consumer form the true standard error
    ``SE = sqrt(brier_variance / n)`` — used by the asymmetric shrink instead of
    the conservative max-dispersion bound ``SE ≤ 1/(2·√n)``.

    ``brier_delta`` = practitioner − practice (NEGATIVE = better-calibrated than
    the practice; POSITIVE = worse). A phase with too few grounded points on
    either side returns ``{"status": "insufficient_data", ...}``.

    This is the raw signal only. The credibility-weighted fold — shrink a
    thin-``n`` practitioner's divergence toward the practice prior (Bühlmann
    ``w = n/(n+k)``, hard floor at ``n < n_min``, asymmetric on
    better-than-practice claims) — is autonomy's lane, applied when the
    divergence feeds arbitration (PRACTITIONER_DELIBERATION_MODEL §3/§6). The
    seam is deliberate: empirica-core surfaces, autonomy weights.
    """
    practitioner = get_practitioner_brier_profile(session_id, ai_id, db, lookback=lookback)
    practice = get_brier_profile(ai_id, db, lookback=lookback)
    out: dict = {}
    for phase in ("noetic", "praxic", "combined"):
        p = practitioner.get(phase, {})
        a = practice.get(phase, {})
        if "brier_score" not in p or "brier_score" not in a:
            out[phase] = {
                "status": "insufficient_data",
                "practitioner_n": p.get("n_predictions", p.get("n", 0)),
                "practice_n": a.get("n_predictions", a.get("n", 0)),
            }
            continue
        out[phase] = {
            "brier_delta": round(p["brier_score"] - a["brier_score"], 4),
            "reliability_delta": round(p["reliability"] - a["reliability"], 4),
            "practitioner_brier": p["brier_score"],
            "practice_brier": a["brier_score"],
            "practitioner_n": p["n_predictions"],
            "practice_n": a["n_predictions"],
            # Per-side Brier variance (B5) → the consumer forms the true SE
            # = sqrt(brier_variance / n) for the asymmetric shrink, instead of
            # the max-dispersion bound. Weighting stays autonomy's lane (B7).
            "practitioner_brier_variance": p.get("brier_variance"),
            "practice_brier_variance": a.get("brier_variance"),
        }
    return out


def _find_brier_section(lines: list[str]) -> tuple[int, int]:
    """Find start/end indices of the brier_calibration section in a YAML file."""
    section_start = -1
    section_end = -1
    in_section = False

    for i, line in enumerate(lines):
        if "# Brier calibration" in line and section_start == -1:
            section_start = i
        elif line.strip().startswith("brier_calibration:"):
            if section_start == -1:
                section_start = i
            in_section = True
        elif in_section and line.strip() and not line.startswith(" ") and not line.startswith("\t"):
            section_end = i
            break

    if in_section and section_end == -1:
        section_end = len(lines)

    return section_start, section_end


def export_brier_to_breadcrumbs(
    ai_id: str,
    db,
    git_root: str | None = None,
) -> bool:
    """Export Brier score profile to .breadcrumbs.yaml as a 'brier_calibration:' section.

    Adds a parallel section alongside learning_trajectory and grounded_calibration.
    Called automatically after POSTFLIGHT to keep Brier data fresh.

    Args:
        ai_id: AI identifier (e.g., 'claude-code')
        db: Database connection with calibration_trajectory access
        git_root: Git repository root (auto-detects if None)

    Returns:
        True if Brier data was written successfully
    """
    import os
    import subprocess
    from datetime import datetime

    if not git_root:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                git_root = result.stdout.strip()
            else:
                return False
        except Exception:
            return False

    breadcrumbs_path = os.path.join(git_root, ".breadcrumbs.yaml")

    profile = get_brier_profile(ai_id, db)
    if not profile:
        return False

    # Check that at least one phase has real data (not just insufficient_data)
    has_data = any(
        phase_data.get("brier_score") is not None
        for phase_data in profile.values()
        if isinstance(phase_data, dict) and phase_data.get("status") != "insufficient_data"
    )
    if not has_data:
        return False

    # Build YAML block
    timestamp = datetime.now().isoformat()
    lines = [
        "\n# Brier calibration (auto-updated by Empirica post-test verification)\n",
        "brier_calibration:\n",
        f'  last_updated: "{timestamp}"\n',
        f"  ai_id: {ai_id}\n",
        '  note: "Murphy (1973) decomposition: BS = Reliability - Resolution + Uncertainty"\n',
    ]

    for phase in ["noetic", "praxic", "combined"]:
        data = profile.get(phase, {})
        lines.append(f"  {phase}:\n")

        if data.get("status") == "insufficient_data":
            n = data.get("n", 0)
            lines.append("    status: insufficient_data\n")
            lines.append(f"    samples: {n}\n")
            continue

        if not data or "brier_score" not in data:
            lines.append("    status: no_data\n")
            continue

        lines.append(f"    brier_score: {data['brier_score']:.4f}\n")
        lines.append(f"    reliability: {data['reliability']:.4f}\n")
        lines.append(f"    resolution: {data['resolution']:.4f}\n")
        lines.append(f"    uncertainty: {data['uncertainty']:.4f}\n")
        lines.append(f"    n_predictions: {data['n_predictions']}\n")
        lines.append(f"    trend: {data.get('trend', 'stable')}\n")

    yaml_block = "".join(lines)

    # Read existing file, find/replace brier_calibration section
    try:
        existing_lines = []
        if os.path.exists(breadcrumbs_path):
            with open(breadcrumbs_path) as f:
                existing_lines = f.readlines()

        section_start, section_end = _find_brier_section(existing_lines)

        if section_start >= 0:
            new_lines = existing_lines[:section_start] + [yaml_block] + existing_lines[section_end:]
        elif existing_lines:
            new_lines = existing_lines + [yaml_block]
        else:
            new_lines = [yaml_block]

        with open(breadcrumbs_path, "w") as f:
            f.writelines(new_lines)

        return True
    except Exception as e:
        logger.debug(f"Failed to export Brier calibration to breadcrumbs: {e}")
        return False

# Training AIs with Empirica

Empirica's epistemic transaction data — the measurement cycles that track AI self-awareness during real work — doubles as a high-quality training dataset for fine-tuning models on epistemic self-awareness.

## Why This Works

Every epistemic transaction captures a complete belief-update cycle:

1. **PREFLIGHT** — AI self-assesses before work (13 vectors)
2. **CHECK** — Sentinel gates noetic→praxic transition (with reasoning)
3. **POSTFLIGHT** — AI self-assesses after work (13 vectors + delta)
4. **Grounded verification** — Deterministic service observations compared to belief vectors

This produces training examples where the model learns to:
- Accurately assess its own knowledge state
- Update beliefs based on evidence
- Distinguish what it knows from what it assumes
- Calibrate confidence against objective outcomes

### Theoretical Grounding

Two recent papers validate this approach:

**OpenAI (March 2026)** — "Reasoning Models Struggle to Control their Chains of Thought" (arxiv/2603.05706): Models cannot fake their chain-of-thought reasoning (0.1–2.8% controllability vs 60%+ for final output). This means epistemic self-assessments captured during CoT are genuine signals, not performative.

**Google (2025)** — "Bayesian teaching enables probabilistic reasoning in LLMs" (Nature s41467-025-67998-6): Training via an uncertainty-struggling assistant produces better calibrated models than training from an all-knowing oracle. Empirica's data IS that uncertainty-struggling assistant — real work with real uncertainty, not synthetic examples.

## The Training Export Command

```bash
# Export from current project
empirica training-export --output-path epistemic_training.jsonl

# Export from ALL projects in workspace (recommended for dataset size)
empirica training-export --workspace --output-path full_dataset.jsonl

# Filter by AI model
empirica training-export --workspace --ai-id empirica --output-path claude_data.jsonl

# Exclude noetic artifacts (smaller records)
empirica training-export --no-artifacts --output-path vectors_only.jsonl

# Require more vector coverage per record
empirica training-export --min-vectors 8 --output-path high_coverage.jsonl

# JSON output mode (for programmatic use)
empirica training-export --workspace --output json
```

## JSONL Record Format

Each line is one epistemic transaction:

```json
{
  "session_id": "abc-123",
  "ai_id": "empirica",
  "project_id": "empirica",
  "transaction_id": "tx-456",
  "preflight_ts": "2026-01-15T10:30:00",
  "postflight_ts": "2026-01-15T11:45:00",

  "preflight_vectors": {
    "know": 0.4, "do": 0.2, "context": 0.5, "clarity": 0.6,
    "coherence": 0.7, "signal": 0.5, "density": 0.3,
    "state": 0.4, "change": 0.1, "completion": 0.0,
    "impact": 0.2, "engagement": 0.8, "uncertainty": 0.6
  },

  "postflight_vectors": {
    "know": 0.8, "do": 0.7, "context": 0.9, "clarity": 0.8,
    "coherence": 0.8, "signal": 0.7, "density": 0.6,
    "state": 0.7, "change": 0.6, "completion": 0.9,
    "impact": 0.7, "engagement": 0.9, "uncertainty": 0.2
  },

  "delta": {
    "know": 0.4, "do": 0.5, "context": 0.4, "clarity": 0.2,
    "coherence": 0.1, "signal": 0.2, "density": 0.3,
    "state": 0.3, "change": 0.5, "completion": 0.9,
    "impact": 0.5, "engagement": 0.1, "uncertainty": -0.4
  },

  "preflight_meta": {
    "current_phase": "NOETIC",
    "notes": "Starting investigation of auth module"
  },

  "postflight_meta": {
    "current_phase": "PRAXIC",
    "notes": "Implemented OAuth flow, tests passing",
    "tool_call_count": 47
  },

  "postflight_reasoning": "Completed auth module refactor...",

  "check_decisions": [
    {
      "timestamp": "2026-01-15T11:00:00",
      "vectors": {"know": 0.7, "uncertainty": 0.3, "completion": 0.5, "clarity": 0.7},
      "decision": "proceed",
      "gate_passed": true
    }
  ],

  "grounded_calibration": {
    "calibration_score": 0.82,
    "grounded_coverage": 0.75,
    "evidence_count": 12,
    "calibration_gaps": {"know": 0.15, "completion": -0.1},
    "sources_available": ["pytest", "git_metrics", "goal_completion"]
  },

  "noetic_artifacts": {
    "findings": [
      {"finding": "OAuth token refresh uses stale cache", "impact": 0.8, "subject": "auth"}
    ],
    "unknowns": [
      {"unknown": "Rate limiting behavior under load", "resolved": false, "impact": 0.6}
    ],
    "dead_ends": [
      {"approach": "JWT validation via middleware", "why_failed": "Incompatible with SSO flow", "impact": 0.5}
    ],
    "mistakes": [
      {"mistake": "Forgot to invalidate old tokens", "why_wrong": "Security hole", "prevention": "Add token revocation to checklist", "root_cause_vector": "do"}
    ],
    "decisions": [
      {"choice": "Use refresh token rotation", "rationale": "Better security posture", "reversibility": "exploratory"}
    ]
  }
}
```

## Dataset Structure

| Field | Description | Training Signal |
|-------|-------------|-----------------|
| `preflight_vectors` | Self-assessment before work | Input: "given this state..." |
| `postflight_vectors` | Self-assessment after work | Target: "...this is what changed" |
| `delta` | Vector differences | Learning magnitude per dimension |
| `check_decisions` | Sentinel gate results mid-work | Decision-making under uncertainty |
| `grounded_calibration` | Objective vs self-assessed | Reward signal — was the AI honest? |
| `noetic_artifacts` | What was discovered/failed/decided | Rich context for the belief update |

### The Reward Signal

`grounded_calibration.calibration_score` is a belief divergence metric — NOT a reward signal. It measures how much the AI's belief vectors diverge from what deterministic services observe (test results, git metrics, goal completion, artifact counts). Lower scores indicate beliefs more aligned with observations. This divergence informs where work discipline may need attention (more noetic work? better artifact logging?), not where vector values need adjusting.

`calibration_gaps` per vector shows where the AI is systematically miscalibrated, enabling targeted training on specific epistemic dimensions.

## Dataset Statistics

As of March 2026 (from a real multi-month deployment):

- **851 transactions** across 14 project databases
- **179 with grounded calibration** (objective verification data)
- **450 with noetic artifacts** (findings, unknowns, dead-ends, mistakes, decisions)
- **500 with CHECK decisions** (Sentinel gate results with reasoning)

The dataset grows naturally as Empirica is used. No synthetic data generation needed.

## Training Approaches

### 1. Supervised Fine-Tuning (SFT)

Train on the full transaction record. The model learns to produce accurate self-assessments given work context.

**Input:** Task description + preflight context + noetic artifacts
**Target:** Postflight vectors + delta + reasoning

### 2. Calibration Training (RLHF/DPO)

Use `grounded_calibration.calibration_score` as the reward signal. The model learns that honest self-assessment is rewarded over inflated confidence.

**Preferred:** Transactions where `calibration_score > 0.8` (well-calibrated)
**Rejected:** Transactions where `calibration_score < 0.5` (poorly calibrated)

### 3. Sentinel Training

Train a lightweight model to replicate the Sentinel's CHECK decision. Uses `check_decisions` data — given partial vectors mid-work, should the AI proceed to action or continue investigating?

### 4. Epistemic Self-Distillation

Use Empirica transactions from a strong model (e.g., Claude Opus) to fine-tune a smaller model (e.g., a local 7B) on epistemic self-awareness. The smaller model inherits the larger model's calibration patterns without needing the same compute budget for every inference.

## Validation

### BullshitBench

[BullshitBench](https://github.com/petergpt/bullshit-benchmark) measures AI pushback against nonsense — 100 prompts across 5 domains with 13 manipulation techniques. An epistemically trained model should score significantly higher on pushback than baseline, because it has learned to distinguish "I know this" from "I'm confabulating."

### Calibration Trajectory

Compare `calibration_report --trajectory` before and after fine-tuning. A well-trained model should show:
- Lower average calibration gap
- Fewer instances of overconfidence on `know` and `completion`
- Higher uncertainty acknowledgment on novel tasks

## Privacy and Data Handling

Training data contains:
- Vector measurements (numerical, non-sensitive)
- Session/transaction IDs (anonymizable)
- Project IDs and AI IDs (strip or anonymize before sharing)
- Noetic artifacts: findings, mistakes, dead-ends (may contain domain-specific content)

For external use, filter with `--no-artifacts` to export vectors-only records, or post-process to redact project-specific content.

## Architecture

```
sessions.db (per project)
  ├── reflexes table → PREFLIGHT/CHECK/POSTFLIGHT vectors
  ├── grounded_verifications → objective calibration
  ├── project_findings, project_unknowns, etc. → noetic artifacts
  └── decisions_made, mistakes_made → epistemic intent

workspace.db (global)
  └── global_projects → trajectory_path → finds all project DBs

training-export command
  ├── Single project: reads local sessions.db
  └── --workspace: iterates ALL project DBs via workspace.db
       → Outputs matched (preflight, postflight) pairs as JSONL
```

## Next Steps

- **Grow the dataset**: Every Empirica session adds transactions automatically
- **Cross-model training**: Export data from different AI models (Claude, Gemini, Qwen) to build model-agnostic epistemic awareness
- **Domain-specific fine-tuning**: Filter by project type/domain for specialized calibration
- **Benchmark integration**: Automated BullshitBench runs pre/post fine-tuning

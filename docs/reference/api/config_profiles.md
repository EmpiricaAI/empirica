# Investigation Profiles & Goal Scope API

**Modules:** `empirica.config.profile_loader`, `empirica.config.goal_scope_loader`
**Stability:** Beta

Two advisory configurations. An **investigation profile** holds the thresholds and styles for one way of working (how much
to investigate, when to proceed, how to reassess). A **goal scope recommendation** turns a vector reading into a suggested
breadth, duration and coordination for a goal. Neither decides anything on its own: the AI and the Sentinel can override.

---

## Investigation profiles

Profiles are read from `empirica/config/investigation_profiles.yaml`. It ships five: `high_reasoning_collaborative`,
`autonomous_agent`, `critical_domain`, `exploratory` and `balanced`.

```python
from empirica.config.profile_loader import get_profile_loader, load_profile, select_profile

loader = get_profile_loader()  # one ProfileLoader per process, reading investigation_profiles.yaml
print(loader.list_profiles())

profile = load_profile("balanced")  # ValueError if there is no such profile
print(profile.action_thresholds.confidence_proceed_min, profile.investigation.max_rounds)

chosen = select_profile(domain="medical")  # by explicit name, then domain, then model, else the default
print(chosen.name)
```

`ProfileLoader(config_path=None)` also offers `get_profile(name)` (None when missing), `validate_constraints(profile)`
(a list of problems), `export_profile(name, path)` and `import_profile(path, name=None)`.

### InvestigationProfile

| Field | Type |
|---|---|
| `name`, `description` | `str` |
| `investigation` | `InvestigationConstraints` |
| `action_thresholds` | `ActionThresholds` |
| `tuning` | `TuningParameters` |
| `strategy` | `StrategyConfig` |
| `learning` | `LearningConfig` |

`to_dict()` serialises it, enums as their values.

### The parts, with their defaults

| Class | Fields (default) |
|---|---|
| `InvestigationConstraints` | `max_rounds` (None, no limit), `confidence_threshold` (0.65), `confidence_threshold_dynamic` (False), `tool_suggestion_mode` (`SUGGESTIVE`), `allow_novel_approaches` (True), `require_tool_approval` (False), `encourage_experimentation` (False) |
| `ActionThresholds` | `uncertainty_high` (0.75), `clarity_low` (0.45), `foundation_low` (0.45), `confidence_proceed_min` (0.65), `override_allowed` (True), `escalate_on_uncertainty` (False) |
| `TuningParameters` | `confidence_weight`, `foundation_weight`, `comprehension_weight`, `execution_weight`, `uncertainty_weight` (all 1.0) |
| `StrategyConfig` | `domain_detection` (`HYBRID`), `tool_selection` ("ai_guided"), `gap_prioritization` ("balanced") |
| `LearningConfig` | `postflight_mode` (`GENUINE_REASSESSMENT`), `confidence_gain_calculation` ("evidence_based"), `require_validation` (False) |
| `UniversalConstraints` (all profiles) | `engagement_gate` (0.60), `coherence_min` (0.50), `density_max` (0.90), `change_min` (0.50), `max_tool_calls_per_round` (10), `investigation_timeout_seconds` (3600), `log_all_assessments`, `log_tool_calls` (True) |

A profile in the YAML overrides these; `load_profile("balanced")` shows the values actually in force.

### Enums

- `ToolSuggestionMode`: `LIGHT`, `SUGGESTIVE`, `GUIDED`, `PRESCRIBED`, `INSPIRATIONAL`
- `DomainDetection`: `REASONING`, `PLUGIN_ASSISTED`, `HYBRID`, `DECLARED`, `EMERGENT`
- `PostflightMode`: `GENUINE_REASSESSMENT`, `COMPARATIVE_ASSESSMENT`, `FULL_AUDIT_TRAIL`, `REFLECTION`

---

## Goal scope recommendations

Patterns are read from `empirica/config/mco/goal_scopes.yaml`: a vector reading is matched to the closest pattern
(`knowledge_leader`, `learning_mode`, `complexity_signals`, `coordination_heavy`, `rapid_iteration`, `production_focused`,
`web_project_design`, `session_continuation`).

```python
from empirica.config.goal_scope_loader import get_scope_recommendations, validate_scope_coherence

rec = get_scope_recommendations({"know": 0.85, "uncertainty": 0.3, "clarity": 0.80})
print(rec)
# {'breadth': 0.7, 'duration': 0.6, 'coordination': 0.4, 'pattern': 'knowledge_leader', 'match_score': 1.0,
#  'rationale': 'High knowledge + confidence enables broader scope work', 'context_applied': False}

print(validate_scope_coherence({"breadth": 0.9, "duration": 0.1, "coordination": 0.5}))
# {'coherent': True, 'warnings': [], 'suggestions': [], 'severity': 'none'}
```

`get_scope_recommendations(epistemic_vectors, context=None)` returns None when no pattern matches; `context` adjusts the
result (and sets `context_applied`). `GoalScopeLoader(config_path=None)` exposes the same two calls as methods, plus
`list_available_patterns()` (pattern name to description).

---

## Implementation files

- `empirica/config/profile_loader.py`: the profile classes, `ProfileLoader` and the module functions
- `empirica/config/investigation_profiles.yaml`: the shipped profiles
- `empirica/config/goal_scope_loader.py`: `GoalScopeLoader` and the module functions
- `empirica/config/mco/goal_scopes.yaml`: the scope patterns

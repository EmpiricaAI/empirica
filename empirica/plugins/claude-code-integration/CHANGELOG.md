# Changelog

All notable changes to the Empirica Integration Plugin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

> This file stops at 1.8.0. Plugin changes since then are recorded in the repository's root `CHANGELOG.md`.

## [1.8.0] - 2026-04-09

### Changed
- **Sentinel gate** — domain-aware threshold scaling via `DomainRegistry`.
  `_get_domain_scaled_thresholds()` adjusts uncertainty threshold by
  `coverage_min` from the domain checklist. `PREFLIGHT` gains `domain`,
  `criticality`, and `predicted_check_outcomes` fields.
- **Pre-compact hook** — graceful degradation (exit 0, empty JSON) when
  project root unresolvable, instead of blocking compact.

### Fixed
- Test isolation: `EMPIRICA_INSTANCE_ID=test-{pid}` prevents test
  pollution of live `instance_projects` files.

## [1.0.0] - 2025-12-28

### Added
- **PreCompact Hook**: Automatically saves epistemic snapshot before memory compact
  - Captures current checkpoint state
  - Saves MCO (Meta-Agent Configuration Object) settings
  - Stores findings, unknowns, goals, dead ends counts
  - Auto-commits git changes before snapshot

- **SessionStart Hook**: Automatically restores context after memory compact
  - Loads project bootstrap (findings, unknowns, goals from SQLite + git)
  - Restores MCO configuration from pre-compact snapshot
  - Calculates and displays epistemic drift metrics
  - Adaptive context depth based on uncertainty

- **SessionEnd Hook**: Curates old snapshots for storage efficiency
  - Removes low-impact snapshots (>30 days old, impact <0.5)
  - Retains high-impact snapshots indefinitely
  - Prevents unbounded growth of ref-docs directory

- **Auto-Detection**: No environment variables needed
  - Automatically finds latest active `claude-code` session
  - Uses session resolver with AI ID pattern matching
  - Graceful fallback if no Empirica session exists

### Features
- **MCO Configuration Preservation**: Bias corrections, investigation budgets, thresholds preserved across compacts
- **Drift Detection**: Quantifies epistemic drift (know, uncertainty, engagement, impact, completion vectors)
- **Adaptive Bootstrap**: Loads context depth based on detected drift level
- **Git Integration**: Auto-commits before compact, references git state in snapshots
- **Token Efficiency**: ~97.5% reduction vs manual context reconstruction

### Technical Details
- Hooks written in Python 3
- Integrates with Empirica CLI commands (`project-bootstrap`, `calibration-report`)
- Compatible with Claude Code hook system (PreCompact, SessionStart, SessionEnd)
- Timeout protection: 30s for PreCompact/SessionStart, 15s for SessionEnd

### Documentation
- Comprehensive README with troubleshooting
- Installation guide for manual setup
- Distribution guide for marketplace submission
- MCO integration architecture documentation

### Requirements
- Empirica installed (`pip install empirica` or from source)
- Active Empirica session (`empirica session-create --ai-id claude-code`)
- Claude Code with plugin support

## [Unreleased]

### Planned
- [ ] Support for multiple concurrent sessions
- [ ] Web UI for snapshot browsing
- [ ] Export snapshots to external format (JSON, Markdown)
- [ ] Configurable snapshot retention policies
- [ ] Integration with Empirica web dashboard
- [ ] Metrics dashboard for drift tracking over time

---

## Version History

- **1.0.0** (2025-12-28): Initial release with PreCompact, SessionStart, SessionEnd hooks

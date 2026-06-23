# Contributing to Empirica

Thanks for your interest in contributing. This guide covers the workflow, conventions, and tooling you'll need.

## Branch strategy

```
main (stable, production-ready)
  ├── develop (integration branch)
  │   ├── feature/your-feature-name
  │   ├── bugfix/issue-description
  │   └── experimental/research-idea
  └── hotfix/critical-bug (emergency fixes from main)
```

| Branch | Purpose | Direct commits | Install command |
|---|---|---|---|
| `main` | Production-ready, stable | Not allowed (PR + review) | `pip install git+https://github.com/EmpiricaAI/empirica.git@main` |
| `develop` | Integration of new features | Allowed for maintainers | `pip install git+https://github.com/EmpiricaAI/empirica.git@develop` |
| `feature/*` | New work | Free-form on your branch | — |
| `hotfix/*` | Emergency fixes from main | Maintainers only | — |

---

## Code ownership (CODEOWNERS)

Some areas have dedicated maintainers and route via `.github/CODEOWNERS`:

| Path | Reviewer | Why |
|---|---|---|
| `packaging/chocolatey/` | `@kars85` | He owns the chocolatey.org listing for `empirica` — the push API key is his |
| `*.ps1` (anywhere) | `@kars85` | Windows PowerShell scripts |
| `docs/windows/` | `@kars85` | Windows-specific docs |

Touching these paths in a PR auto-requests his review. For direct-to-develop commits in his areas (rare), tag `@kars85` on the commit.

---

## Commit messages

Conventional-prefix style with explanatory body:

```
feat(scope): short imperative summary

Body explains WHY the change is needed and what it does at the
right level of abstraction. Wrap at ~72 chars. Reference issues
or prior commits where relevant.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
```

Common prefixes: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`, `revert`. Scope optional but useful (`feat(sentinel):`, `fix(statusline):`, `chore(release):`).

When AI-pair-programmed, include the `Co-Authored-By` trailer so attribution is honest.

---

## Local setup

```bash
git clone https://github.com/EmpiricaAI/empirica.git
cd empirica
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
empirica setup-claude-code  # Optional: wires the plugin into ~/.claude
```

`pip install -e ".[dev]"` brings in `pytest`, `ruff`, `pyright`, and the test dependencies declared in `pyproject.toml`.

---

## Testing & quality

Before opening a PR:

```bash
ruff check empirica/ empirica-mcp/ tests/   # Lint — must be clean
pytest tests/ -q                             # Test suite — should be all green
empirica compliance-report                   # 12-check audit (lint, complexity,
                                             # type_safety, tech_docs, repo_hygiene,
                                             # release_chain, discipline, calibration,
                                             # epistemic_audit, etc.)
```

Compliance must report all 12 checks passing for release-ready work. For day-to-day contributions, lint + tests are the minimum bar.

The full suite takes ~5 minutes. Most changes only need a targeted run:
```bash
pytest tests/test_<area>.py -q
```

---

## Release process

Maintainers only. The `scripts/release.py` flow handles version sweep, build, test, and publish across all channels (PyPI, Docker, GitHub release, Homebrew, Chocolatey).

```bash
# 1. Bump version in pyproject.toml + empirica-mcp/pyproject.toml
# 2. Update CHANGELOG.md (move [Unreleased] → [vX.Y.Z])
# 3. Update README.md "What's New in vX.Y.Z" section
# 4. Sweep version strings across the repo
python scripts/release.py --version-only --old-version <prev>

# 5. Prepare: merge develop→main, build, smoke test, run pytest
python scripts/release.py --prepare

# 6. Review the prepared artifacts (dist/*.tar.gz, *.whl, SHA256)
# 7. Publish to all channels
python scripts/release.py --publish
```

`--publish` is irreversible (PyPI doesn't allow re-uploading the same version). Always `--prepare` first and review.

Full release docs: `docs/human/developers/RELEASE_PROCESS.md` (if it exists yet — otherwise the script's own docstrings).

---

## Using Empirica to develop Empirica (dogfooding)

We use Empirica's epistemic transaction workflow to manage Empirica's own development. This is both how we eat our own dog food and how we generate real-world calibration data.

### Why dogfood

- If it helps us build the framework, it'll help others use it
- Edge cases and UX problems surface naturally
- Our sessions become reference examples
- Calibration data accrues against real shipping work

### Cascade workflow for non-trivial work

```bash
empirica session-create --ai-id <your-ai-id>

# Open the measurement window
empirica preflight-submit - <<EOF
{"task_context": "...", "vectors": {"know": 0.5, ...}, "reasoning": "..."}
EOF

# Investigate / log findings, unknowns, decisions / read code
empirica finding-log --finding "..." --epistemic-source search --impact 0.6

# Gate the noetic→praxic transition
empirica check-submit - <<EOF
{"vectors": {...}, "current_phase": "praxic", "reasoning": "..."}
EOF

# ... write code, run tests, commit ...

# Close the measurement window
empirica postflight-submit - <<EOF
{"vectors": {...}, "reasoning": "..."}
EOF
```

For trivial fixes (typos, single-line config), skip the cascade — just commit with a clean message.

### Meta-principles

1. Use Empirica for non-trivial contributions
2. Track findings and unknowns honestly (not optimistically)
3. Demonstrate value by using it ourselves
4. If we wouldn't use it, why should users?

Full meta-development guide: [`.empirica-project/README.md`](.empirica-project/README.md).

---

## Where else to look

| Topic | Path |
|---|---|
| Architecture overview | [`docs/architecture/`](docs/architecture/) |
| End-user docs | [`docs/human/end-users/`](docs/human/end-users/) |
| Developer docs | [`docs/human/developers/`](docs/human/developers/) |
| API reference | [`docs/reference/`](docs/reference/) |
| Changelog | [`CHANGELOG.md`](CHANGELOG.md) |
| Plugin (Claude Code integration) | [`empirica/plugins/claude-code-integration/`](empirica/plugins/claude-code-integration/) |

---

## Reporting issues

GitHub issues: https://github.com/EmpiricaAI/empirica/issues

For sensitive issues (security, credentials), email `david@getempirica.com` directly.

# CI/CD Architecture

**Status:** v1 — initial scaffolding, shipped in v1.9.6 follow-up work.
Patterned on ecodex's two-workflow split, translated to Python.

---

## Workflow inventory

| Workflow | Trigger | Purpose |
|---|---|---|
| `.github/workflows/ci.yml` | push/PR to `main` + `develop` | Pre-merge gate: lint + types + tests + compliance |
| `.github/workflows/release.yml` | tag push `v*.*.*` | Publish: PyPI ×2 + Docker ×2 + Homebrew tap + GitHub release |
| `.github/workflows/dependency-scan.yml` | weekly + on `pyproject.toml` PRs | pip-audit CVE check |
| `.github/dependabot.yml` | weekly | Auto-PRs for outdated + vulnerable deps |

---

## CI gate (ci.yml)

Three jobs run on every push / PR to `main` or `develop`:

| Job | What it runs | Failure semantics |
|---|---|---|
| `lint-and-types` | `ruff check`, `ruff format --check` (informational), `pyright` | Hard fail on ruff errors or pyright errors |
| `test` | `pytest` on Python **3.10** + **3.13** | Hard fail on any failed test |
| `compliance` | `empirica compliance-report` + `pip-audit` | Compliance informational; `pip-audit` hard fail on CVE |

**Matrix scope decision.** pyproject claims 3.10–3.13. We test the
endpoints (3.10 = min, 3.13 = current) and skip 3.11/3.12. If a bug
shows up that's specific to those intermediate versions, add them then.
Each matrix cell is ~5min, so 2 cells = ~10min wall-clock with parallel
runners. Adding 3.11/3.12 doubles the runner cost for marginal coverage.

**Why `compliance` is informational.** Empirica's compliance pipeline
includes calibration/epistemic-audit checks that score the work done in
the repo. On a CI run that hasn't run any AI work, scores can dip below
threshold purely because there's no fresh PREFLIGHT/POSTFLIGHT data. The
pipeline result is uploaded as an artifact so reviewers can see it, but
it doesn't gate merge. **TODO:** once we have a clean signal (e.g. only
the deterministic checks — lint/complexity/pyright/repo-hygiene), tighten
to hard-fail.

**Why `pip-audit` hard-fails.** Known CVEs in the dep tree are a real
risk. The compliance pipeline runs pip-audit as part of its set, but the
dedicated step in ci.yml gives a fast, clear signal at the dependency-
update touchpoint.

---

## Release pipeline (release.yml)

Local + remote split. Three local phases, and the first one is deliberately
separate:

| Phase | Branch | Does |
|---|---|---|
| `--docs` | **develop** | Authors the release-facing docs: version sweep across 27 files, README's What's New from CHANGELOG, CLI reference regen. **Commits nothing** — the diff is for review, then committed with the bump. |
| `--prepare` | main | Merge, build, gates (import / ruff / pyright / pip-audit / pytest / issue-tracker). **Writes no tracked docs** — it *verifies* the `--docs` output is committed and refuses otherwise. |
| `--publish` | main | **Tag + push only.** The tag triggers this workflow, which publishes every channel. `--local-artifacts` restores local publishing as an escape hatch for when CI is down. |

**Why `--docs` exists** (2026-08-05). `--prepare` used to author those files
itself, *after* checking out main. That one choice produced three defects:
main accumulated a README and CLI reference develop had never seen, so every
release merge conflicted on exactly those two files; the bump had to be
committed before the sync could run, leaving a window where `pyproject` led the
README — which is how 1.13.4 shipped advertising *"What's New in 1.13.3"*; and
the sync could `warning()`-and-return while the release continued regardless.

Authoring is reasoning-adjacent work and belongs on develop where the author
and the review are. The release path does deterministic work and *gates* on the
authoring being done.

**Trigger:** push of a `v*.*.*` tag to `main`.

**Jobs (parallel where possible):**

```
                    ┌─→ pypi-empirica
                    │
build → artifacts ──┼─→ pypi-empirica-mcp
                    │
                    ├─→ docker (Debian + Alpine)
                    │
                    └─→ github-release ← (gate: both pypi jobs)
                                     ↓
                              homebrew-tap update
```

### PyPI publishing — OIDC trusted publishers

Both packages use [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/) via the
official `pypa/gh-action-pypi-publish` action. **No `PYPI_API_TOKEN`
secret needed** once trusted publishers are configured on
[pypi.org/manage/account/publishing/](https://pypi.org/manage/account/publishing/).

Setup (one-time, per package):
1. PyPI → Your projects → empirica → Settings → Publishing
2. Add trusted publisher:
   - Owner: `Nubaeon`
   - Repository: `empirica`
   - Workflow: `release.yml`
   - Environment: `pypi-empirica`
3. Same for `empirica-mcp` with environment `pypi-empirica-mcp`

The workflow declares both environments via `environment.name` on the
corresponding job. The `id-token: write` permission at the top of the
workflow allows OIDC token issuance to PyPI.

### Docker — uses Docker Hub PAT

Two image variants per release:
- `nubaeon/empirica:<version>` + `:latest` from `Dockerfile` (Debian slim)
- `nubaeon/empirica:<version>-alpine` from `Dockerfile.alpine` (security-hardened)

Required secrets:
- `DOCKERHUB_USERNAME` — Docker Hub login name
- `DOCKERHUB_TOKEN` — Docker Hub access token with push scope

Missing secrets → step skips with a `::warning::` (forks don't break).

### Homebrew tap

The workflow checks out `EmpiricaAI/homebrew-tap`, updates `Formula/empirica.rb`
with the new version + sha256, commits + pushes.

Required secret:
- `HOMEBREW_TAP_TOKEN` — PAT with `repo` scope on `EmpiricaAI/homebrew-tap`

Missing token → step skips with a `::warning::`.

### Chocolatey

**Not in this workflow** — Chocolatey publishes need a Windows runner +
the `choco` CLI. Handled by kars85 via the upstream Windows tooling
loop. Out of scope for the Linux-runner-based release pipeline.

---

## Dependency scanning (dependency-scan.yml + dependabot.yml)

**Weekly schedule:** Monday 06:17 UTC for pip-audit, Monday 07:00–08:00
UTC for Dependabot PRs (staggered 30min apart per ecosystem to avoid
runner contention).

**On-demand:** `workflow_dispatch` lets us trigger manually. The
workflow also runs on PRs that touch `pyproject.toml` so dependency
changes get audited at review time.

**Grouped Dependabot updates:**
- `pinned-security` group: cryptography, gitpython, lxml, pydantic,
  python-dotenv, python-multipart, requests — these are the deps with
  active CVE pins. Group so one PR moves the whole security floor at
  once instead of 7 fragmented PRs.
- `lint-and-test` group: ruff, pyright, pytest* — tooling updates that
  always need to be bumped together to avoid flux in the lint baseline.
- Everything else: individual PRs, capped at 5 open per ecosystem.

---

## Secrets reference

| Secret | Used by | Required? |
|---|---|---|
| `DOCKERHUB_USERNAME` | release.yml `docker` job | No (skips with warning) |
| `DOCKERHUB_TOKEN` | release.yml `docker` job | No |
| `HOMEBREW_TAP_TOKEN` | release.yml `homebrew` job | No |
| `GITHUB_TOKEN` | release.yml `github-release` job | Auto-provided |

**PyPI:** uses OIDC trusted publishing — no secret token. If trusted
publisher setup is deferred, add `PYPI_API_TOKEN` and switch the publish
step to:
```yaml
with:
  password: ${{ secrets.PYPI_API_TOKEN }}
```

---

## Local + CI alignment

The local `scripts/release.py` is the source of truth for the release
pipeline. `release.yml` should match its behavior:

| Step | Local (`release.py --docs/--prepare/--publish`) | CI (`release.yml`) |
|---|---|---|
| Version sweep across 27 files | `--docs` (on develop, reviewed + committed) | Not needed (already committed) |
| README What's New + CLI reference | `--docs` (on develop); `--prepare` only verifies | Not needed (already committed) |
| Build sdist + wheel | `--prepare` | `build` job |
| Test gate | `--prepare` runs `pytest` | `ci.yml` already ran on pre-tag commit |
| Publish PyPI ×2 | CI only (`--local-artifacts` to force local) | `pypi-*` jobs (OIDC) |
| Create git tag | `--publish` | Tag is the trigger |
| Push Docker ×2 | CI only | `docker` job |
| GitHub release | CI only | `github-release` job |
| Homebrew tap update | CI only | `homebrew` job |

**Migration: done (2026-08-05).** `--publish` is now tag-and-push only.
The bar this plan set — "verified for a release or two" — was met by
1.13.4, 1.13.5 and 1.13.6 publishing cleanly through CI, and the cost of
waiting was visible: with both paths live, every release raced on the
GitHub release (`a release with the same tag name already exists`,
recovered with `--clobber`, three times in one day).

`--local-artifacts` keeps the old path for when CI is unavailable. It is
an escape hatch, not an alternative — running both is what caused the
race. A test asserts release.yml still defines every channel job, so
removing one fails there rather than at the next release.

---

## What's not here (deferred)

- **compliance-report.yml as recurring scheduled** — folded into `ci.yml`
  per PR instead. Per-PR feedback is more actionable than weekly emails.
- **Codecov / coverage gates** — coverage is informational in
  pytest-cov; no badge or PR-block on coverage delta yet.
- **Pre-commit.ci** — we have pre-commit hooks locally; not running on
  CI yet. ruff + pyright cover most of what pre-commit would catch.
- **Self-hosted runners** — only if Docker Hub or PyPI rate-limits us.
  No signal of that yet.
- **Cross-platform builds** — empirica is pure Python; single wheel
  serves everything. macOS/Windows users get it transparently via pip.
  Chocolatey publish stays out-of-band (kars85 lane).

---

## Cross-references

- `scripts/release.py` — the local source of truth, see flow in its
  docstring.
- `scripts/release_check.py` — pre-release sanity check.
- Ecodex CI (Rust analog): `~/empirical-ai/ecodex/.github/workflows/`
  — the pattern this design translated from.

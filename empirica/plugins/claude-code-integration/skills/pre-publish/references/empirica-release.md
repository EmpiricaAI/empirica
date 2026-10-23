# Releasing empirica

`pyproject.toml` is the single source of the version. All commands from the repo root on `develop` unless stated.

1. Set the new version in `pyproject.toml` and `empirica-mcp/pyproject.toml`.
2. `python scripts/release.py --version-only --old-version <old> --commit` sweeps every other version string and commits an
   allowlisted set.
3. Write a `## [<new>] - <date>` entry at the top of `CHANGELOG.md` (the README's What's New syncs from the top entry).
4. `python scripts/release.py --docs`: regenerates the CLI reference and README section, does not commit. Review, then
   `git add -u && git commit`.
5. Push; wait for CI on THAT commit by its full run id (`gh run list --branch develop`, `gh run view <id>`).
6. `python scripts/release.py --prepare`: checks docs, merges to local `main`, builds, runs ruff, pyright, pip-audit and the import
   smoke tests, and skips the full suite when CI is green on the same commit. It does not push or tag.
7. `python scripts/release.py --publish`: must run on `main`; tags and pushes. `release.yml` owns every channel from here. Only on
   the owner's explicit go for this release.
8. Find the release run: `gh run list --workflow release.yml`, wait on its id, list its jobs.
9. `python scripts/release.py --verify`: checks the artifacts on PyPI (both packages), Docker (both tags), the GitHub release, the
   Homebrew tap, and a fresh `pip -U` install. PyPI's simple index can lag the job by minutes.
10. Refresh this machine: `pip install -e . --no-deps`, `pipx upgrade empirica-mcp --pip-args="--no-cache-dir"`,
    `empirica setup-claude-code --force` (deploys the prompt, hooks and skills to `~/.claude`; running sessions keep what they
    loaded). Restart persistent listener units only if listener code changed.

Known quirks: the compliance report's `release_chain` check reads FAIL until the version is published; Chocolatey is skipped where
there is no `choco` CLI; `release.py` returns you to `develop` after publish.

Gates to run before step 6 (the skill's steps 2 to 7): `empirica compliance-report --dep-audit --security`, then the sweep.

## Reading the deterministic tier

- `compliance-report` security lists counts only: identify and judge a "critical" finding by running
  `semgrep --config p/owasp-top-ten empirica/` directly.
- A check that returns `null` on a pass may not have run: confirm the check actually executed before trusting it.
- Suppress a scanner false positive on its one line with the justification beside it, never by widening the gate.
- Do not announce until PyPI's simple index carries the version; its JSON API updates minutes earlier.
- Run the suite the way CI does, `pytest tests/ -m "not integration" -n auto`. The integration tests invoke the real CLI in the checkout
  and write goal, task and session notes into its real notes namespace; a full local run adds about six refs each time.

---
name: pre-publish
description: "Use when about to push, tag, release or publish code other people will run — 'ready to release', 'cut a release', 'before I push', 'pre-publish', 'is this safe to ship'. The ordered gate sequence: scope the diff, deterministic gates, an independent read-only sweep with every finding traced, docs and code gaps, hot-path timing, a clean re-run on the exact commit, then the cut. Carries the traps that green CI hides."
version: 1.0.0
---

# Pre-publish

Load this **before** you push a release or publish, not after something ships wrong.

Deterministic gates catch what someone thought to check. An independent sweep catches what nobody did. On 1.14.13, CI was green on the
exact commit and the compliance report passed 15 of 16 checks; three read-only reviewers then flagged 23 candidates and 9 held up
under tracing, including a new destructive command whose reference check was missing a whole store. No test could have failed on
that, because the defect was an absence.

## The sequence (the order matters)

1. **Scope.** `git diff <last-tag>..HEAD --stat`. Group the changed files into 3 to 5 areas. Note whether any hook, or the
   PREFLIGHT/CHECK/POSTFLIGHT path, changed: that decides step 5.
2. **Deterministic tier: a tool's job, not yours.** Lint, types, full tests, dependency CVE audit, secret scan, docs drift. Fix
   failures; do not waive them. A CVE with a published fix is a floor bump, not a note. In empirica this is
   `empirica compliance-report --dep-audit --security` plus `release.py --prepare`
   (`references/empirica-release.md`).
3. **Independent sweep.** One read-only reviewer per area, in parallel, each with the brief in `references/sweep-brief.md`. They do
   not edit, apply, or run anything that mutates. The pattern table they apply is eat-the-broccoli, Phase 2.
4. **Trace every finding yourself.** A reviewer's verdict is an uncalibrated self-report. For each finding: open the file:line,
   reproduce or trace it end to end, ask whether it is by design, then either fix it failing-test-first or log it as a goal or
   unknown. Keep three numbers and report them: candidates, confirmed, fixed. Say plainly which confirmed ones are still open.
5. **Hot path.** If hooks or PREFLIGHT/CHECK/POSTFLIGHT changed, time the real path on the last tag's copy against the new one, same
   inputs, 15 runs each. A per-call cost regression blocks the cut; a removal that was already short-circuited is neutral, not a
   saving.
6. **Gaps.** Code against docs: new verbs and flags documented, removed names still referenced anywhere (docs, prompts, skills,
   scripts), the changelog's top entry is this release.
7. **Re-run on the exact commit you will release.** Green CI on a neighbouring commit is not green on this one.
8. **Cut and verify** (`references/empirica-release.md`). Wait for the release run by its id; verify the artifacts, not the job
   status.

## Rules earned, each from an incident

- **A second pass that is not clean means the first was wrong.** After any bulk repair or apply, re-run the plan. 342 of 1,727 items
  still disagreeing exposed a bug in the repair itself; the unit tests had never exercised the condition.
- **Grep for an existing guard before writing a new one.** The check the sweep wanted already existed one class over, with a comment
  explaining why it mattered.
- **A new destructive command is swept before it touches real data.** Enumerate what references the thing it deletes from the
  schema (every table with that id as a column, every external store), and treat an unreachable store as unknown, never zero.
- **A test that mocks the boundary pins the defect.** Check payloads against the callee's own published schema.
- **A claim about current code cites HEAD, not a report.** A public forecast item described a gap a peer had reported against older vendored
  copies; the repo had fixed it three weeks earlier. For each claim in README, ROADMAP or a changelog, run the command that shows it today
  (`git log -S<symbol> --since=<report date>`, a count, a grep) and write its evidence kind beside it.
- **Run the oldest Python you claim.** `release.py --prepare` parses the tree under `requires-python`'s floor through `uv`. ruff targets py310 but
  parses with the newest grammar, so a 3.12-only f-string passed lint, format and a 3.14 suite and broke CI on 3.11. If the gate prints
  `NOT VERIFIED`, the floor was not checked: say so in the report instead of reading silence as clean.
- **A falsifier tests the prediction it guards, and someone outside can evaluate it.** Write the claim as the check's own sentence; if you cannot, they
  are two claims.
- **Wait by run id, never by short SHA.** A short SHA matches nothing and the wait never ends.
- **Do not publish on a go you inferred.** Tag and push only on the owner's explicit word for this release.

## Report

Done / Next / Waiting, with the counts from step 4 and the commit the release was cut from. Anything you left open is named, not
implied.

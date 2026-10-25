# Sweep brief (one per area)

Fill in the angle brackets. Send each reviewer the same brief with its own scope; run them in parallel.

> You are doing a READ-ONLY pattern hunt (standard depth, changed scope) before a release. Do not edit, commit, or run anything that
> mutates state: no `--apply`, no git writes, nothing under the user's home or the repo. Reading, grep, running pytest on specific
> files, and `git diff` / `git show` are fine. Repo: <path>, branch <branch>. Scope = `git diff <last-tag>..HEAD` restricted to:
> <files for this area>.
>
> Read the pattern table in the eat-the-broccoli skill (Phase 2) and apply it to the CHANGED lines and what they touch. Context you
> need: <two or three sentences on what this area now does and the invariant most likely to be wrong>.
>
> For each candidate defect give: file:line, the failing input or state, the observable wrong result, and whether it could be BY
> DESIGN (check comments and tests). Mark each CONFIRMED (you traced or reproduced it end to end) or PLAUSIBLE; a suspicion is not
> CONFIRMED. At most 10 findings, most severe first, compact. If you find nothing real, say so and list what you checked. Under 500
> words.

Add one job per area where it applies: public text that makes claims (README, ROADMAP, changelog Highlights): re-run the command behind each claim
against HEAD, ask whether each falsifier tests the prediction it guards, and try the generators that write it with an input shape they were not written for
(a sentinel after the span must survive); hooks that run on every prompt (anything crashing, dangling, or adding per-call cost), and
docs/prompt accuracy (sections or behaviours referred to that no longer exist).

The reviewers' verdicts are uncalibrated. Their value is the candidates; the confirmation is yours.

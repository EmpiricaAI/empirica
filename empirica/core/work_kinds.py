"""What kind of change a goal or task intends (goal 1dfb9911; David confirmed the six on 2026-11-19).

The forecast's plan mode shows the recent mix by kind and recommends one for the next release; the human decides. The first four
are kinds of product change, the last two cover the work around it, because about 31% of recent goals fit none of the four
(releases, sweeps, inbox, gardening, measurements). ``work_type`` (code, docs, debug ...) is a different axis: it weights the
evidence for one transaction, not what the change is for.
"""

from __future__ import annotations

KINDS: tuple[str, ...] = ("fix", "refine", "simplify", "expand", "investigate", "sustain")

#: The Conventional Commits type a kind's commits usually take (commit messages stay the measurable record of the mix).
COMMIT_TYPE: dict[str, str] = {
    "fix": "fix",  # defects and issues (corrective)
    "refine": "refactor",  # same capability, better: clarity, robustness; perf work takes `perf`
    "simplify": "refactor",  # remove, consolidate, delete dead code (preventive)
    "expand": "feat",  # new capability or direction
    "investigate": "docs",  # measure, reproduce, decide: the outcome is usually an artifact or a written result
    "sustain": "chore",  # release, ops, hygiene, coordination, assurance
}

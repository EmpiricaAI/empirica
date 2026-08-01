"""Splicing a generated section into ``.breadcrumbs.yaml``.

Two exporters write parallel sections into the same file — ``grounded_calibration``
(grounded_calibration.py) and ``brier_calibration`` (dynamic_thresholds.py) — and
both had independently copy-pasted the same splice arithmetic, so they had the
same bug twice.

Each block is authored leading with a newline so the section is visually
separated from whatever precedes it, and each splices at the *comment* line that
opens the section. Those two facts compose badly: the blank line produced by the
previous write sits at ``section_start - 1``, which is inside the preserved
prefix, and the new block then contributes another one. One blank line accrued
per export, per section, forever. Measured on this practice: 3766 blank lines out
of 3934 (96% of the file), in two contiguous runs sitting immediately before
their own section headers.

Nothing failed loudly — YAML ignores blank lines, so the file stayed valid and
parsed correctly the whole time. It just grew without bound, and the file is read
at session start by every practice.

The fix belongs in one place precisely because the duplication is what let the
same defect exist twice.
"""

from __future__ import annotations


def splice_section(
    existing_lines: list[str],
    yaml_block: str,
    section_start: int,
    section_end: int,
) -> list[str]:
    """Return ``existing_lines`` with the located section replaced by ``yaml_block``.

    Args:
        existing_lines: Current file contents, as returned by ``readlines()``.
        yaml_block: The rendered section. Expected to lead with a newline — that
            newline is the section's one separator, which is why any trailing
            blank lines already in the prefix have to go.
        section_start: Index of the section's first line (its comment header), or
            -1 when the section is not present yet.
        section_end: Index one past the section's last line.

    Trailing blank lines are trimmed from the prefix in both the replace and the
    append path, so the separator count is a property of ``yaml_block`` alone
    rather than of how many times this has run before. That makes the operation
    idempotent: splicing the same block twice yields the same file, which is the
    property the old code lacked.
    """
    if section_start >= 0:
        # A header found with no section body leaves section_end at -1, which
        # would slice the last line back in as the suffix and duplicate it.
        end = section_end if section_end >= 0 else len(existing_lines)
        prefix = existing_lines[:section_start]
        suffix = existing_lines[end:]
    else:
        prefix = list(existing_lines)
        suffix = []

    while prefix and not prefix[-1].strip():
        prefix.pop()

    if not prefix:
        # Nothing precedes the section — either an empty file or one that was
        # entirely accumulated blanks. The leading newline would put a blank
        # line at the very top, the one place a separator is not wanted.
        yaml_block = yaml_block.lstrip("\n")

    return prefix + [yaml_block] + suffix

"""Splicing a section into .breadcrumbs.yaml must be idempotent.

Found while verifying an unrelated fix: `.breadcrumbs.yaml` had 3766 blank lines
out of 3934 — 96% of the file — in two contiguous runs sitting immediately before
the `grounded_calibration` and `brier_calibration` headers.

Each exporter authors its block leading with a newline (the section separator)
and splices at the *comment* line opening the section. Those compose badly: the
blank produced by the previous write lives at `section_start - 1`, inside the
preserved prefix, and the new block contributes another. One blank per export,
per section, unbounded.

Nothing ever failed. YAML ignores blank lines, so the file stayed valid and
parsed correctly throughout — it just grew forever, and it is read at session
start by every practice. The two exporters had independently copy-pasted the same
splice arithmetic, so the same defect existed twice; the helper under test
replaces both copies.
"""

from __future__ import annotations

from empirica.core.post_test.breadcrumbs_yaml import splice_section

BLOCK = "\n# Section header (auto-updated)\nmy_section:\n  value: 1\n"
BLOCK_V2 = "\n# Section header (auto-updated)\nmy_section:\n  value: 2\n"


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _find(lines: list[str]) -> tuple[int, int]:
    """Locate the section the way both real exporters do."""
    start = end = -1
    in_section = False
    for i, line in enumerate(lines):
        if "# Section header" in line and start == -1:
            start = i
        elif line.strip().startswith("my_section:"):
            if start == -1:
                start = i
            in_section = True
        elif in_section and line.strip() and not line.startswith((" ", "\t")):
            end = i
            break
    if in_section and end == -1:
        end = len(lines)
    return start, end


def _rewrite(lines: list[str], block: str) -> list[str]:
    """One export cycle. Re-splits first because the real callers read the file
    back off disk with readlines() each time — splice_section returns the block
    as a single element, and treating that as one 'line' on the next pass would
    test something the production path never does."""
    lines = _lines("".join(lines))
    start, end = _find(lines)
    return _lines("".join(splice_section(lines, block, start, end)))


def test_repeated_splices_do_not_accumulate_blank_lines():
    """POSITIVE CONTROL — the reproduction, in miniature.

    The old arithmetic grew the file by one line per rewrite. Fifty rewrites of
    the same block must produce the same file as one.
    """
    lines = _lines("other: value\n")

    once = _rewrite(lines, BLOCK)
    many = once
    for _ in range(50):
        many = _rewrite(many, BLOCK)

    assert many == once
    assert sum(1 for line in many if not line.strip()) == 1, "blank separators accumulated"


def test_an_existing_run_of_blank_lines_is_cleaned_up():
    """The 3766 already in the live file must not need a manual repair — the
    next export should absorb them, since each run is contiguous and sits
    immediately before its own header."""
    bloated = _lines("other: value\n" + "\n" * 400 + "# Section header (auto-updated)\nmy_section:\n  value: 1\n")

    result = _rewrite(bloated, BLOCK_V2)

    assert sum(1 for line in result if not line.strip()) == 1
    assert "".join(result) == "other: value\n\n# Section header (auto-updated)\nmy_section:\n  value: 2\n"


def test_the_section_separator_survives():
    """NEGATIVE CONTROL: trimming every blank would jam the header onto the
    preceding line. Exactly one separator is the point — not zero."""
    result = _rewrite(_lines("other: value\n"), BLOCK)

    assert "".join(result) == "other: value\n\n# Section header (auto-updated)\nmy_section:\n  value: 1\n"


def test_content_before_and_after_the_section_is_preserved():
    """NEGATIVE CONTROL: this file holds many parallel sections. A splice that
    dropped a neighbour would be far worse than the leak it fixes."""
    original = _lines(
        "before: 1\n\n# Section header (auto-updated)\nmy_section:\n  value: 1\n\nafter: 2\n  nested: 3\n"
    )

    result = "".join(_rewrite(original, BLOCK_V2))

    assert result.startswith("before: 1\n")
    assert "after: 2\n  nested: 3\n" in result
    assert "value: 2" in result
    assert "value: 1" not in result


def test_appending_to_a_file_without_the_section():
    """First-ever export: the section is appended with its separator."""
    result = "".join(splice_section(_lines("other: value\n"), BLOCK, -1, -1))

    assert result == "other: value\n\n# Section header (auto-updated)\nmy_section:\n  value: 1\n"


def test_an_empty_file_does_not_start_with_a_blank_line():
    """The top of a file is the one place the leading separator is unwanted."""
    result = "".join(splice_section([], BLOCK, -1, -1))

    assert result.startswith("# Section header")


def test_a_header_with_no_body_does_not_duplicate_the_last_line():
    """Both real finders leave section_end at -1 when they see the comment but
    never the key. Slicing on -1 would splice the final line back in as the
    suffix, duplicating it — a corruption worse than the leak."""
    lines = _lines("before: 1\n\n# Section header (auto-updated)\n")

    result = "".join(splice_section(lines, BLOCK, 2, -1))

    assert result == "before: 1\n\n# Section header (auto-updated)\nmy_section:\n  value: 1\n"


def test_a_file_of_only_blank_lines_is_not_left_with_them():
    """Degenerate but reachable: a file that is nothing but accumulated blanks."""
    result = "".join(splice_section(_lines("\n" * 20), BLOCK, -1, -1))

    assert result.startswith("# Section header")


def test_both_real_exporters_use_the_shared_helper():
    """Source guard. The duplication is what let one defect exist twice — if a
    copy of the splice arithmetic reappears, this fails."""
    from pathlib import Path

    import empirica.core.post_test.dynamic_thresholds as dt
    import empirica.core.post_test.grounded_calibration as gc

    for module in (dt, gc):
        src = Path(module.__file__).read_text(encoding="utf-8")
        assert "splice_section(" in src, f"{module.__name__} no longer uses the shared helper"
        assert "+ [yaml_block] +" not in src, f"{module.__name__} has re-inlined the splice arithmetic"

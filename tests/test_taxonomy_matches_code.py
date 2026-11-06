"""The taxonomy's tables for project types, work types and compliance domains say what the code says.

docs/reference/TAXONOMY.md section 4.6 is the one place that explains project type, project domain, work type, compliance domain and criticality
together (David, 2026-11-06: "there was no clear doc on this and there should be so we can refer to it going forward"). A reference that drifts from
the code is worse than none, so each table is checked against its source: the type list and its two mappings, the PREFLIGHT pattern for work types,
and the domain files that ship.
"""

from __future__ import annotations

import re
from pathlib import Path

from empirica.cli.validation import PreflightInput
from empirica.core import project_types as pt

ROOT = Path(__file__).parent.parent
DOC = (ROOT / "docs" / "reference" / "TAXONOMY.md").read_text()
SECTION = DOC[DOC.index("### 4.6 ") : DOC.index("## 5. Systems")]


TYPE_TABLE = SECTION[SECTION.index("**Project types.**") : SECTION.index("**How the project type is used.**")]


def _rows(first_cell_pattern: str) -> dict[str, list[str]]:
    """Rows of the project-types table only: later tables reuse names such as `research` and `operations`."""
    rows = {}
    for line in TYPE_TABLE.splitlines():
        m = re.match(rf"\| `({first_cell_pattern})` \|(.*)\|\s*$", line)
        if m:
            rows[m.group(1)] = [c.strip() for c in m.group(2).split("|")]
    return rows


def test_every_project_type_has_a_row_with_the_mappings_the_code_uses():
    rows = _rows(r"[a-z-]+")
    for t in pt.PROJECT_TYPES:
        assert t in rows, f"project type {t} has no row in TAXONOMY 4.6"
        domain, criticality = pt.default_work_frame(t)
        assert rows[t][1] == pt.calibration_domain(t), f"{t}: calibration domain in the doc is {rows[t][1]}"
        assert rows[t][2] == f"{domain}, {criticality}", f"{t}: default frame in the doc is {rows[t][2]}"


def test_no_row_names_a_project_type_the_code_does_not_have():
    listed = set(re.findall(r"^\| `([a-z-]+)` \|", TYPE_TABLE, flags=re.M))
    assert listed == set(pt.PROJECT_TYPES)


def test_the_legacy_values_named_in_the_doc_are_the_ones_the_code_still_reads():
    for legacy in pt.LEGACY_TYPES:
        assert f"`{legacy}`" in SECTION


def test_the_work_types_are_the_ones_preflight_accepts():
    pattern = next(m.pattern for m in PreflightInput.model_fields["work_type"].metadata if hasattr(m, "pattern"))
    accepted = set(pattern.strip("^$()").split("|"))
    table = SECTION[SECTION.index("**Work types.**") : SECTION.index("**Domains.**")]
    listed = set(re.findall(r"^\| `([a-z-]+)` \|", table, flags=re.M))
    assert listed == accepted


def test_the_domains_are_the_files_that_ship():
    shipped = {p.stem for p in (ROOT / "empirica" / "config" / "domains").glob("*.yaml")}
    table = SECTION[SECTION.index("**Domains.**") : SECTION.index("**One domain, set in two places.**")]
    listed = set(re.findall(r"^\| `([a-z-]+)` \|", table, flags=re.M))
    assert listed == shipped


def test_the_section_no_longer_describes_a_free_text_project_domain_as_current():
    """One meaning for "domain" (David, 2026-11-06). The label table holds one domain row, and it points at the list."""
    table = SECTION[: SECTION.index("**Project types.**")]
    rows = re.findall(r"^\| \*\*([^*]+)\*\* \|", table, flags=re.M)
    assert rows == ["Project type", "Work type", "Domain", "Criticality"]
    assert "Free text" not in table

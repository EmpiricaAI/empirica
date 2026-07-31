"""The drift detector must itself be falsifiable.

A freshness auditor that reports clean because it cannot see is the exact pattern
it exists to catch — and I have shipped that before: a drift-check whose regex only
matched a verb followed by a flag, silently missing every positional-arg
invocation. It reported clean for months.

So every check here gets BOTH controls:

  · a POSITIVE control — known-drifted input it must FIRE on
  · a NEGATIVE control — known-clean input it must stay silent on

A detector shown only to run clean has been shown nothing. Two real defects in this
script were found exactly this way, by sampling its own output against ground truth
I could falsify:

  1. `@pytest.fixture` functions named `test_*` were flagged as assertion-free
     tests — 6 of 44 hits, a 14% false-positive rate.
  2. A `--` SQL comment above a column definition swallowed that column, so a
     fixture that visibly HAD `is_resolved` was reported as missing it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "test_freshness_audit.py"
_spec = importlib.util.spec_from_file_location("test_freshness_audit", _SCRIPT)
assert _spec and _spec.loader
TFA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(TFA)


# ── column parsing ────────────────────────────────────────────────────


def test_columns_are_extracted_from_a_plain_body():
    cols = TFA._fixture_columns("id TEXT PRIMARY KEY, finding TEXT NOT NULL, impact REAL DEFAULT 0.5")

    assert cols == {"id", "finding", "impact"}


def test_a_sql_comment_does_not_swallow_the_column_beneath_it():
    """REGRESSION. A `-- note` has no terminating comma, so the parser read
    comment-plus-column as one part and took `--` as the name — reporting a column
    absent from a table that visibly had it."""
    body = """
        id TEXT PRIMARY KEY,
        -- Lifecycle columns (migrations 057/061), added 2026-07-31.
        is_resolved INTEGER DEFAULT 0, resolution TEXT
    """

    assert TFA._fixture_columns(body) == {"id", "is_resolved", "resolution"}


def test_constraint_clauses_are_not_mistaken_for_columns():
    body = "id TEXT, project_id TEXT, PRIMARY KEY (id), FOREIGN KEY (project_id) REFERENCES projects(id)"

    assert TFA._fixture_columns(body) == {"id", "project_id"}


def test_a_nested_paren_does_not_split_a_column():
    """`CHECK(status IN ('a','b'))` contains commas INSIDE parens — splitting on
    them would invent columns named after enum values."""
    body = "id TEXT, status TEXT CHECK(status IN ('unverified','verified','falsified'))"

    assert TFA._fixture_columns(body) == {"id", "status"}


# ── stale-fixture: both controls ──────────────────────────────────────


def _write(tmp_path, name: str, text: str) -> Path:
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture
def scan_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(TFA, "TESTS_DIR", tmp_path)
    monkeypatch.setattr(TFA, "REPO", tmp_path)
    return tmp_path


def test_positive_control_a_stale_fixture_IS_flagged(scan_dir):
    """The detector must FIRE on a fixture missing lifecycle columns."""
    _write(
        scan_dir,
        "test_stale.py",
        'X = """CREATE TABLE project_findings (id TEXT, finding TEXT, created_timestamp REAL);"""',
    )
    real = {"project_findings": {"id", "finding", "created_timestamp", "is_resolved", "resolution_kind"}}

    found = TFA.check_stale_fixtures(real)

    assert len(found) == 1
    assert set(found[0]["missing_columns"]) == {"is_resolved", "resolution_kind"}


def test_negative_control_a_current_fixture_is_NOT_flagged(scan_dir):
    """Silence must mean something — a fixture carrying the lifecycle columns
    produces no finding, so a clean report is informative rather than vacuous."""
    _write(
        scan_dir,
        "test_fresh.py",
        'X = """CREATE TABLE project_findings (id TEXT, finding TEXT, is_resolved INTEGER, resolution_kind TEXT);"""',
    )
    real = {"project_findings": {"id", "finding", "is_resolved", "resolution_kind"}}

    assert TFA.check_stale_fixtures(real) == []


def test_a_table_with_no_lifecycle_contract_is_ignored(scan_dir):
    """Only tables with a KNOWN lifecycle are judged. Flagging every minimal
    fixture would bury the signal — minimal fixtures are legitimate."""
    _write(scan_dir, "test_other.py", 'X = """CREATE TABLE sessions (id TEXT);"""')

    assert TFA.check_stale_fixtures({"sessions": {"id", "created_at", "extra"}}) == []


# ── unfalsifiable / tautological: both controls ───────────────────────


def test_positive_control_an_assertion_free_test_IS_flagged(scan_dir):
    _write(scan_dir, "test_a.py", "def test_does_nothing():\n    x = 1\n")

    found = TFA.check_unfalsifiable_and_tautological()

    assert [f["check"] for f in found] == ["unfalsifiable-test"]


def test_negative_control_a_real_assertion_is_NOT_flagged(scan_dir):
    _write(scan_dir, "test_b.py", "def test_real():\n    assert 1 + 1 == 2\n")

    assert TFA.check_unfalsifiable_and_tautological() == []


def test_a_fixture_named_test_something_is_not_a_test(scan_dir):
    """REGRESSION — 6 of 44 hits were `@pytest.fixture` functions named `test_*`,
    which contain no assertion by design."""
    _write(
        scan_dir,
        "test_c.py",
        "import pytest\n\n@pytest.fixture\ndef test_repo(tmp_path):\n    return tmp_path\n",
    )

    assert TFA.check_unfalsifiable_and_tautological() == []


def test_the_parametrised_fixture_form_is_also_recognised(scan_dir):
    """`@pytest.fixture(scope="module")` is a Call node, not an Attribute."""
    _write(
        scan_dir,
        "test_d.py",
        'import pytest\n\n@pytest.fixture(scope="module")\ndef test_thing():\n    return 1\n',
    )

    assert TFA.check_unfalsifiable_and_tautological() == []


def test_pytest_raises_counts_as_a_real_check(scan_dir):
    """A test whose whole contract is "this raises" has no `assert` and is not
    unfalsifiable."""
    _write(
        scan_dir,
        "test_e.py",
        "import pytest\n\ndef test_raises():\n    with pytest.raises(ValueError):\n        int('x')\n",
    )

    assert TFA.check_unfalsifiable_and_tautological() == []


@pytest.mark.parametrize("body", ["assert True", "assert 1 == 1", "assert x == x"])
def test_positive_control_tautological_asserts_ARE_flagged(scan_dir, body):
    _write(scan_dir, "test_f.py", f"def test_tauto():\n    x = 5\n    {body}\n")

    found = TFA.check_unfalsifiable_and_tautological()

    assert any(f["check"] == "tautological-assert" for f in found), f"{body!r} cannot fail and must be flagged"


def test_negative_control_a_comparison_of_two_things_is_not_tautological(scan_dir):
    _write(scan_dir, "test_g.py", "def test_ok():\n    a, b = 1, 1\n    assert a == b\n")

    assert TFA.check_unfalsifiable_and_tautological() == []

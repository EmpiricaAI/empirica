"""Prevention measurement report (S5) — read-only per-family measurement surface.

Composes read + aggregate per outcome_family + exposed/shadow-arm split. Raw
exposed-arm rates only (NOT the causal ATE). Fail-open. See
docs/architecture/PREVENTION_MEASUREMENT_SPEC.md.
"""

from __future__ import annotations

import sqlite3
import types

from empirica.core.prevention import (
    emit_fabrication_exposure,
    emit_prevention_exposure,
    prevention_report,
)
from empirica.data.migrations.migrations import (
    migration_058_prevention_events,
    migration_059_prevention_outcome_family,
)


def _db():
    conn = sqlite3.connect(":memory:")
    migration_058_prevention_events(conn.cursor())
    migration_059_prevention_outcome_family(conn.cursor())
    conn.commit()
    return types.SimpleNamespace(conn=conn)


def test_empty_report():
    r = prevention_report(_db(), "sess")
    assert r["total_events"] == 0
    assert r["families"] == []
    assert r["by_family"] == {}
    assert "NOT the causal ATE" in r["disclaimer"]


def test_report_splits_by_family():
    db = _db()
    emit_prevention_exposure(db, "sess", "tx", pattern_key="P", subject_key="s1")
    emit_fabrication_exposure(db, "sess", "tx", pattern_key="fab:P", subject_key="c1")
    r = prevention_report(db, "sess")
    assert r["total_events"] == 2
    assert set(r["families"]) == {"prevention", "fabrication"}
    assert r["by_family"]["prevention"]["total"] == 1
    assert r["by_family"]["fabrication"]["total"] == 1


def test_report_shadow_arm_split():
    db = _db()
    emit_prevention_exposure(db, "sess", "tx", pattern_key="P", subject_key="s1", shadow=False)
    emit_prevention_exposure(db, "sess", "tx", pattern_key="P", subject_key="s2", shadow=True)
    r = prevention_report(db, "sess")
    assert r["exposed_arm"] == 1
    assert r["shadow_arm"] == 1


def test_report_beneficiary_independent_surfaced_per_family():
    db = _db()
    # a cross-practice prevention (author != beneficiary)
    emit_prevention_exposure(
        db,
        "sess",
        "tx",
        pattern_key="P",
        subject_key="s1",
        author_practice="A",
        beneficiary_practice="B",
    )
    r = prevention_report(db, "sess")
    assert "beneficiary_independent" in r["by_family"]["prevention"]
    assert "beneficiary_independent" in r["overall"]


def test_report_fail_open_on_missing_table():
    conn = sqlite3.connect(":memory:")  # no prevention_events table
    db = types.SimpleNamespace(conn=conn)
    r = prevention_report(db, "sess")
    assert r["total_events"] == 0
    assert r["by_family"] == {}
    assert "NOT the causal ATE" in r["disclaimer"]

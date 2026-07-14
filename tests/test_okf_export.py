"""OKF export — project the empirica epistemic graph into an Open Knowledge
Format bundle (github.com/GoogleCloudPlatform/knowledge-catalog).

These tests exercise the pure projection (build_concept / render_concept /
render_index) directly, plus the bundle writer with the git-notes reader mocked,
so they run offline. The load-bearing OKF invariants: every concept file has a
`type` frontmatter field, concept IDs are the file path minus .md, relationships
are markdown links, and empirica's calibration metadata rides as `empirica_*`
extension keys (which a plain OKF consumer can ignore).
"""

from __future__ import annotations

import yaml

from empirica.core import okf_export as okf

FINDING_SPEC = next(s for s in okf.ARTIFACT_SPECS if s["ns"] == "findings")
DEADEND_SPEC = next(s for s in okf.ARTIFACT_SPECS if s["ns"] == "dead_ends")
DECISION_SPEC = next(s for s in okf.ARTIFACT_SPECS if s["ns"] == "decisions")


def _frontmatter(rendered: str) -> dict:
    """Parse the YAML frontmatter block out of a rendered concept."""
    assert rendered.startswith("---\n")
    _, fm, _body = rendered.split("---\n", 2)
    return yaml.safe_load(fm)


# ── build_concept ────────────────────────────────────────────────────────────
def test_build_concept_required_type_and_extensions():
    c = okf.build_concept(
        FINDING_SPEC,
        "uuid-1",
        {"finding": "JWTs are signed, not encrypted", "impact": 0.7, "created_at": 1_700_000_000, "ai_id": "empirica"},
    )
    assert c is not None
    fm = c["frontmatter"]
    assert fm["type"] == "Finding"  # OKF's one required field
    assert fm["title"].startswith("JWTs are signed")
    assert "empirica" in fm["tags"] and "findings" in fm["tags"]
    assert fm["empirica_id"] == "uuid-1"
    assert fm["empirica_impact"] == 0.7  # calibration rides as an extension key
    assert fm["empirica_ai_id"] == "empirica"
    assert c["concept_id"] == "findings/uuid-1"  # OKF concept-id = path minus .md
    assert c["filename"] == "findings/uuid-1.md"


def test_build_concept_none_when_no_headline():
    assert okf.build_concept(FINDING_SPEC, "x", {"impact": 0.5}) is None


def test_build_concept_body_sections_from_body_keys():
    c = okf.build_concept(
        DEADEND_SPEC,
        "d1",
        {"approach": "Tried passport.js", "why_failed": "Too heavy for JWT-only auth"},
    )
    assert "Tried passport.js" in c["body"]
    assert "## Why it failed" in c["body"]
    assert "Too heavy for JWT-only auth" in c["body"]


def test_build_concept_parent_and_source_links():
    c = okf.build_concept(
        DECISION_SPEC,
        "dec1",
        {
            "choice": "Use JWE",
            "rationale": "JWS leaks at rest",
            "parent_id": "f9",
            "parent_type": "finding",
            "source_refs": ["s1", "s2"],
        },
    )
    targets = {t for _label, t in c["links"]}
    assert "findings/f9" in targets  # parent_type pluralized → namespace
    assert "sources/s1" in targets and "sources/s2" in targets


# ── render_concept ───────────────────────────────────────────────────────────
def test_render_concept_is_valid_frontmatter_and_links():
    c = okf.build_concept(
        DECISION_SPEC,
        "dec1",
        {"choice": "Use Redis", "rationale": "TTL primitives", "parent_id": "f1", "parent_type": "finding"},
    )
    rendered = okf.render_concept(c)
    fm = _frontmatter(rendered)
    assert fm["type"] == "Decision"
    assert "## Related" in rendered
    # OKF relationships are markdown links, bundle-relative (leading /).
    assert "[findings/f1](/findings/f1.md)" in rendered


def test_render_concept_no_related_section_without_links():
    c = okf.build_concept(FINDING_SPEC, "f1", {"finding": "standalone fact"})
    assert "## Related" not in okf.render_concept(c)


# ── render_index ─────────────────────────────────────────────────────────────
def test_render_index_groups_by_type_with_links():
    concepts = [
        okf.build_concept(FINDING_SPEC, "f1", {"finding": "alpha fact"}),
        okf.build_concept(FINDING_SPEC, "f2", {"finding": "beta fact"}),
        okf.build_concept(DECISION_SPEC, "d1", {"choice": "gamma choice", "rationale": "r"}),
    ]
    idx = okf.render_index(concepts)
    assert idx.startswith("---\ntype: Index")
    assert "## Finding (2)" in idx
    assert "## Decision (1)" in idx
    assert "(/findings/f1.md)" in idx


# ── generate_okf_bundle (reader mocked) ──────────────────────────────────────
def test_generate_bundle_writes_files_and_index(tmp_path, monkeypatch):
    fake_notes = {
        "findings": [("f1", {"finding": "A fact", "impact": 0.6})],
        "decisions": [("d1", {"choice": "A choice", "rationale": "because"})],
    }
    monkeypatch.setattr(
        "empirica.cli.command_handlers.artifacts_commands._read_all_notes",
        lambda ws, ns: fake_notes.get(ns, []),
    )
    out = tmp_path / "okf"
    res = okf.generate_okf_bundle(".", out)
    assert res["ok"] is True
    assert res["concept_count"] == 2
    assert (out / "findings" / "f1.md").exists()
    assert (out / "decisions" / "d1.md").exists()
    assert (out / "index.md").exists()
    # Every concept file carries a `type` (the one OKF-required field).
    fm = _frontmatter((out / "findings" / "f1.md").read_text())
    assert fm["type"] == "Finding"
    assert res["by_type"] == {"Finding": 1, "Decision": 1}

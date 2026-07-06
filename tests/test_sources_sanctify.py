"""Source sanctification classifier — dead / duplicate / zombie / valid.

Precedence: dead → duplicate → zombie → valid. Only dead + duplicate are
auto-safe (reversible archival); zombie stays manual review.
"""

from __future__ import annotations

from empirica.core.sources import classify_sources, summarize


def _src(sid, content_hash=None, canonical_path=None, title="t"):
    return {"id": sid, "title": title, "content_hash": content_hash, "canonical_path": canonical_path}


def _verdicts(classifications):
    return {c["id"]: c["verdict"] for c in classifications}


def test_valid_referenced_present_unique():
    c = classify_sources(
        [_src("s1", content_hash="h1")], referenced_ids={"s1"}, hash_counts={"h1": 1}, missing_paths=set()
    )
    assert c[0]["verdict"] == "valid"
    assert c[0]["auto_safe"] is False


def test_zombie_unreferenced():
    c = classify_sources(
        [_src("s1", content_hash="h1")], referenced_ids=set(), hash_counts={"h1": 1}, missing_paths=set()
    )
    assert c[0]["verdict"] == "zombie"
    assert c[0]["auto_safe"] is False  # manual review — may still be citable


def test_duplicate_shared_hash():
    srcs = [_src("s1", content_hash="dup"), _src("s2", content_hash="dup")]
    c = classify_sources(srcs, referenced_ids={"s1", "s2"}, hash_counts={"dup": 2}, missing_paths=set())
    assert all(v == "duplicate" for v in _verdicts(c).values())
    assert all(x["auto_safe"] for x in c)


def test_dead_missing_path():
    c = classify_sources(
        [_src("s1", canonical_path="/gone/x.md", content_hash="h1")],
        referenced_ids={"s1"},
        hash_counts={"h1": 1},
        missing_paths={"/gone/x.md"},
    )
    assert c[0]["verdict"] == "dead"
    assert c[0]["auto_safe"] is True


def test_precedence_dead_beats_duplicate_beats_zombie():
    # a source that is dead AND duplicate AND unreferenced → reported as dead
    c = classify_sources(
        [_src("s1", content_hash="dup", canonical_path="/gone.md"), _src("s2", content_hash="dup")],
        referenced_ids=set(),  # both unreferenced
        hash_counts={"dup": 2},
        missing_paths={"/gone.md"},
    )
    v = _verdicts(c)
    assert v["s1"] == "dead"  # dead wins over duplicate + zombie
    assert v["s2"] == "duplicate"  # duplicate wins over zombie


def test_summarize_counts():
    c = classify_sources(
        [_src("a", content_hash="h"), _src("b"), _src("c", canonical_path="/x", content_hash="h2")],
        referenced_ids={"a"},
        hash_counts={"h": 1, "h2": 1},
        missing_paths={"/x"},
    )
    s = summarize(c)
    assert s["total"] == 3
    assert s["by_verdict"].get("valid") == 1  # a
    assert s["by_verdict"].get("zombie") == 1  # b (no hash, unreferenced)
    assert s["by_verdict"].get("dead") == 1  # c
    assert s["auto_safe"] == 1  # only the dead one


def test_empty():
    assert classify_sources([], set(), {}, set()) == []
    assert summarize([])["total"] == 0

"""Without a build at POSTFLIGHT, a web output directory is graded only if this transaction wrote it.

With no web_evidence.build_command, an old dist/ or public/ on disk was graded (HTML validation, links, assets) as if this
transaction had built it. And the 'web_build' note reached the bundle's source_notes only when the web source returned
zero items, so a bundle that graded stale output also hid that no build ran.
"""

from __future__ import annotations

import os
import subprocess
import time

from empirica.core.post_test import web_collector
from empirica.core.post_test.collector import EvidenceProfile, PostTestCollector
from empirica.core.post_test.web_collector import WebEvidenceCollector


def _page(body: str) -> str:
    return f'<html><head><meta charset="utf-8"><title>t</title></head><body>{body}</body></html>'


def _site(tmp_path, *, written_at: float):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "logo.png").write_bytes(b"\x89PNG")
    (dist / "index.html").write_text(_page('<a href="index.html">self</a><img src="/logo.png">'))
    for f in dist.iterdir():
        os.utime(f, (written_at, written_at))
    return dist


def _no_subprocess(monkeypatch):
    def fake_run(cmd, *a, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(web_collector.subprocess, "run", fake_run)


def test_output_written_before_preflight_is_not_graded(tmp_path, monkeypatch):
    _no_subprocess(monkeypatch)
    preflight = time.time() - 60
    _site(tmp_path, written_at=preflight - 3600)
    c = WebEvidenceCollector(session_id="s", project_path=str(tmp_path), preflight_timestamp=preflight)
    items = c.collect_all()
    assert {i.metric_name for i in items} & {"html_validity", "link_integrity", "asset_integrity"} == set()
    assert "web_output" in c.notes and "before" in c.notes["web_output"]
    assert "web_build" in c.notes


def test_output_written_after_preflight_is_graded(tmp_path, monkeypatch):
    _no_subprocess(monkeypatch)
    preflight = time.time() - 60
    _site(tmp_path, written_at=preflight + 30)
    c = WebEvidenceCollector(session_id="s", project_path=str(tmp_path), preflight_timestamp=preflight)
    names = {i.metric_name for i in c.collect_all()}
    assert {"html_validity", "link_integrity", "asset_integrity"} <= names
    assert "web_output" not in c.notes


def _web_only(monkeypatch):
    """Run PostTestCollector.collect_all with only the web profile's source: the universal sources read a session DB."""
    for name in (
        "_collect_artifact_metrics",
        "_collect_noetic_metrics",
        "_collect_sentinel_metrics",
        "_collect_goal_metrics",
        "_collect_issue_metrics",
        "_collect_triage_metrics",
        "_collect_codebase_model_metrics",
        "_collect_non_git_file_metrics",
    ):
        monkeypatch.setattr(PostTestCollector, name, lambda self: [])
    monkeypatch.setattr(PostTestCollector, "_resolve_profile", lambda self: EvidenceProfile.WEB)


class _DB:
    conn = None

    def close(self):
        pass


def test_the_build_note_reaches_source_notes_even_when_the_web_source_graded_something(tmp_path, monkeypatch):
    _no_subprocess(monkeypatch)
    _web_only(monkeypatch)
    preflight = time.time() - 60
    _site(tmp_path, written_at=preflight + 30)
    pc = PostTestCollector(session_id="s", db=_DB(), phase="praxic", preflight_timestamp=preflight)
    pc._project_root = str(tmp_path)
    bundle = pc.collect_all()
    assert "web" in bundle.sources_available
    assert "build not run" in bundle.source_notes.get("web", "")


def test_the_post_test_collector_passes_its_preflight_to_the_web_collector(tmp_path, monkeypatch):
    _no_subprocess(monkeypatch)
    _web_only(monkeypatch)
    preflight = time.time() - 60
    _site(tmp_path, written_at=preflight - 3600)
    pc = PostTestCollector(session_id="s", db=_DB(), phase="praxic", preflight_timestamp=preflight)
    pc._project_root = str(tmp_path)
    bundle = pc.collect_all()
    assert not any(i.source == "web_validation" or i.metric_name == "html_validity" for i in bundle.items)
    assert "web_output" in bundle.source_notes.get("web", "")


def test_an_opted_in_build_whose_tool_is_missing_says_so_and_grades_no_stale_output(tmp_path, monkeypatch):
    import yaml

    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(
        yaml.dump({"web_evidence": {"build_command": "hugo --minify", "output_dir": "dist"}})
    )

    def missing(cmd, *a, **kw):
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(web_collector.subprocess, "run", missing)
    preflight = time.time() - 60
    _site(tmp_path, written_at=preflight - 3600)
    c = WebEvidenceCollector(session_id="s", project_path=str(tmp_path), preflight_timestamp=preflight)
    assert [i for i in c.collect_all() if i.metric_name == "html_validity"] == []
    assert "not found" in c.notes["web_build"] and "web_output" in c.notes


def test_a_build_that_raises_does_not_make_old_output_fresh():
    """Third review pass: _build_ran was set before the build ran, and a PermissionError escaped every reset."""
    from empirica.core.post_test import web_collector as wc

    src = __import__("inspect").getsource(wc)
    run_at = src.index("result = subprocess.run(", src.index("def _collect_build_verification"))
    assert "self._build_ran = True" not in src[:run_at].split("def _collect_build_verification")[1]

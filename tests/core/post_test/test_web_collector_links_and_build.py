"""Web evidence: links resolve the way a browser resolves them, and POSTFLIGHT does not build the site unasked.

H. Relative links were resolved against the output root, so dist/blog/a.html
   linking b.html looked for dist/b.html and reported a broken link.
I. The collector found its project with `git rev-parse` from cwd (ignoring the
   project its caller knows) and ran an auto-detected full build synchronously,
   up to 120 s, inside every POSTFLIGHT of a web project.
"""

from __future__ import annotations

import subprocess

import yaml

from empirica.core.post_test.web_collector import WebEvidenceCollector


def _page(body: str) -> str:
    return f'<html><head><meta charset="utf-8"><title>t</title></head><body>{body}</body></html>'


def _links(tmp_path, files: dict[str, str]):
    dist = tmp_path / "dist"
    for rel, content in files.items():
        f = dist / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)
    c = WebEvidenceCollector(session_id="s", project_path=str(tmp_path))
    items = c._collect_link_integrity()
    assert len(items) == 1
    return items[0].raw_value


def test_a_relative_link_resolves_against_the_containing_page(tmp_path):
    raw = _links(tmp_path, {"blog/a.html": _page('<a href="b.html">b</a>'), "blog/b.html": _page("")})
    assert raw["broken"] == [] and raw["valid"] == 1


def test_root_relative_parent_relative_and_protocol_relative_links(tmp_path):
    raw = _links(
        tmp_path,
        {
            "index.html": _page(""),
            "blog/b.html": _page(""),
            "blog/a.html": _page(
                '<a href="/blog/b.html">r</a><a href="../index.html">p</a>'
                '<script src="//cdn.example.invalid/x.js"></script><a href="missing.html">m</a>'
            ),
        },
    )
    assert raw["valid"] == 2
    assert raw["external_skipped"] == 1  # //host/x is external, not dist/host/x
    assert [b["link"] for b in raw["broken"]] == ["missing.html"]


def _record_runs(monkeypatch):
    from empirica.core.post_test import web_collector

    calls = []

    def fake_run(cmd, *a, **kw):
        calls.append((cmd, kw.get("cwd")))
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="/somewhere/else\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(web_collector.subprocess, "run", fake_run)
    return calls


def test_the_collector_reads_the_project_root_it_is_given(tmp_path, monkeypatch):
    project = tmp_path / "site"
    (project / "dist").mkdir(parents=True)
    (project / "dist" / "index.html").write_text(_page(""))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    calls = _record_runs(monkeypatch)

    c = WebEvidenceCollector(session_id="s", project_path=str(project))
    assert c._get_project_path() == project
    assert c._get_output_dir() == project / "dist"
    assert not any(cmd[:2] == ["git", "rev-parse"] for cmd, _ in calls)


def test_no_build_runs_without_an_opt_in(tmp_path, monkeypatch):
    (tmp_path / "package.json").write_text('{"scripts": {"build": "vite build"}}')  # auto-detectable
    calls = _record_runs(monkeypatch)
    c = WebEvidenceCollector(session_id="s", project_path=str(tmp_path))
    assert c._collect_build_verification() == []
    assert calls == []
    assert "web_build" in c.notes and "build_command" in c.notes["web_build"]


def test_an_opted_in_build_command_runs(tmp_path, monkeypatch):
    """Positive control: the project's own build_command is run, in the project."""
    (tmp_path / ".empirica").mkdir()
    (tmp_path / ".empirica" / "project.yaml").write_text(
        yaml.dump({"web_evidence": {"build_command": "hugo --minify", "output_dir": "public"}})
    )
    calls = _record_runs(monkeypatch)
    c = WebEvidenceCollector(session_id="s", project_path=str(tmp_path))
    items = c._collect_build_verification()
    assert calls == [(["hugo", "--minify"], str(tmp_path))]
    assert items and items[0].metric_name == "build_success" and items[0].value == 1.0


def test_the_post_test_collector_hands_its_project_root_to_the_web_collector(tmp_path, monkeypatch):
    from empirica.core.post_test.collector import PostTestCollector

    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text(_page('<a href="index.html">self</a>'))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    _record_runs(monkeypatch)

    class _DB:
        conn = None

        def close(self):
            pass

    pc = PostTestCollector(session_id="s", db=_DB())
    pc._project_root = str(tmp_path)
    [(source, fn)] = pc._get_web_collectors()
    assert source == "web"
    items = fn()
    assert {i.metric_name for i in items} >= {"html_validity", "link_integrity"}

"""get_active_project_path stale-mapping guard (cross-harness misbind fix).

On harnesses that don't set EMPIRICA_CWD_RELIABLE (codex/ecodex), the resolver
fell straight to instance_projects — and a stale entry mis-bound the practice
(a session in ecodex-lab resolving to empirica-extension with a frozen vector
snapshot). When cwd is a registered project ROOT that differs from the mapping,
the cwd (physical ground truth) now wins. Claude Code is unaffected: it sets
EMPIRICA_CWD_RELIABLE, so it returns at Priority -1 before the guard.
"""

from __future__ import annotations

import json

import empirica.utils.session_resolver as sr


def _mk_project(root, ai_id: str):
    ed = root / ".empirica"
    ed.mkdir(parents=True, exist_ok=True)
    (ed / "project.yaml").write_text(f"ai_id: {ai_id}\n")
    return root


def _bind_instance_projects(home, instance_id: str, project_path):
    ip = home / ".empirica" / "instance_projects"
    ip.mkdir(parents=True, exist_ok=True)
    (ip / f"{instance_id}.json").write_text(json.dumps({"project_path": str(project_path)}))


def _setup(tmp_path, monkeypatch, *, instance_path, cwd_path, cwd_reliable=False, instance_id="tmux_t"):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sr, "get_instance_id", lambda: instance_id)
    if cwd_reliable:
        monkeypatch.setenv("EMPIRICA_CWD_RELIABLE", "true")
    else:
        monkeypatch.delenv("EMPIRICA_CWD_RELIABLE", raising=False)
    _bind_instance_projects(tmp_path, instance_id, instance_path)
    monkeypatch.chdir(cwd_path)


def test_stale_mapping_guard_prefers_cwd_project(tmp_path, monkeypatch):
    # instance_projects (stale) → extension; but we're standing in ecodex-lab.
    extension = _mk_project(tmp_path / "empirica-extension", "empirica-extension")
    lab = _mk_project(tmp_path / "ecodex-lab", "ecodex-lab")
    _setup(tmp_path, monkeypatch, instance_path=extension, cwd_path=lab)

    assert sr.get_active_project_path() == str(lab)  # cwd wins over the stale mapping


def test_no_override_when_cwd_matches_mapping(tmp_path, monkeypatch):
    proj = _mk_project(tmp_path / "proj", "proj")
    _setup(tmp_path, monkeypatch, instance_path=proj, cwd_path=proj)

    assert sr.get_active_project_path() == str(proj)  # same project → no override


def test_no_override_when_cwd_not_a_project_root(tmp_path, monkeypatch):
    # cwd is a plain dir (no .empirica/project.yaml) — the mapping is authoritative.
    proj = _mk_project(tmp_path / "proj", "proj")
    plain = tmp_path / "somewhere-else"
    plain.mkdir()
    _setup(tmp_path, monkeypatch, instance_path=proj, cwd_path=plain)

    assert sr.get_active_project_path() == str(proj)  # cwd not a project → mapping wins


def test_cwd_reliable_still_returns_cwd_at_priority_minus_1(tmp_path, monkeypatch):
    # Claude Code path: EMPIRICA_CWD_RELIABLE=true → cwd wins at Priority -1,
    # before the guard is ever reached. Unaffected by this change.
    extension = _mk_project(tmp_path / "empirica-extension", "empirica-extension")
    lab = _mk_project(tmp_path / "ecodex-lab", "ecodex-lab")
    _setup(tmp_path, monkeypatch, instance_path=extension, cwd_path=lab, cwd_reliable=True)

    assert sr.get_active_project_path() == str(lab)

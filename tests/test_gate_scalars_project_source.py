"""Per-practice project.yaml source for the artifact-graph gate scalars.

The map's "project.yaml home": a practice flips enforcement on by setting an
``artifact_graph`` block in its (gitignored, per-instance) .empirica/project.yaml
— so one practice enforcing doesn't touch the others. Precedence:
env var (session override) > project.yaml > single-source default.
"""

from __future__ import annotations

import yaml

from empirica.cli.command_handlers import _workflow_shared as ws


def _project(tmp_path, block):
    empdir = tmp_path / ".empirica"
    empdir.mkdir(parents=True, exist_ok=True)
    (empdir / "project.yaml").write_text(yaml.dump({"project_id": "p", "artifact_graph": block}))
    return tmp_path


def _clear_env(monkeypatch):
    for e in (
        "EMPIRICA_ARTIFACT_GRAPH_STRICTNESS",
        "EMPIRICA_ARTIFACT_GRAPH_FLOOR",
        "EMPIRICA_ARTIFACT_GRAPH_PATIENCE",
    ):
        monkeypatch.delenv(e, raising=False)


def test_no_project_block_uses_defaults(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setattr(ws.R, "project_path", staticmethod(lambda: str(tmp_path)))  # no project.yaml
    s = ws._resolve_gate_scalars()
    assert s["strictness"] == 0.75  # peers with no block now enforce by default
    assert s["connectivity_floor"] == 0.34


def test_project_yaml_overrides_default(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    # Values distinct from the (now enforce) default so the project source is
    # provably honored — a practice can tune up OR down via project.yaml.
    root = _project(tmp_path, {"strictness": 0.9, "connectivity_floor": 0.6})
    monkeypatch.setattr(ws.R, "project_path", staticmethod(lambda: str(root)))
    s = ws._resolve_gate_scalars()
    assert s["strictness"] == 0.9
    assert s["connectivity_floor"] == 0.6
    assert s["patience"] == 0.80  # unset key falls back to default


def test_env_wins_over_project_yaml(tmp_path, monkeypatch):
    root = _project(tmp_path, {"strictness": 0.75})
    monkeypatch.setattr(ws.R, "project_path", staticmethod(lambda: str(root)))
    monkeypatch.setenv("EMPIRICA_ARTIFACT_GRAPH_STRICTNESS", "0.1")  # session override
    assert ws._resolve_gate_scalars()["strictness"] == 0.1


def test_malformed_project_block_falls_back(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    root = _project(tmp_path, {"strictness": "loud"})  # unparseable
    monkeypatch.setattr(ws.R, "project_path", staticmethod(lambda: str(root)))
    assert ws._resolve_gate_scalars()["strictness"] == 0.75  # falls back to default


def test_non_dict_block_ignored(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    root = _project(tmp_path, "not-a-dict")
    monkeypatch.setattr(ws.R, "project_path", staticmethod(lambda: str(root)))
    assert ws._read_project_gate_scalars() == {}
    assert ws._resolve_gate_scalars()["strictness"] == 0.75

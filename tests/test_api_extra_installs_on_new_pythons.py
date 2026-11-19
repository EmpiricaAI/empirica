"""empirica[api] stays installable on a Python that fastembed's onnxruntime has no wheel for (autonomy, 2026-11-19).

On Python 3.15 the extra did not resolve at all, and a resolver backtracked a seat to 1.15.1 to satisfy it. fastembed is
gated behind a python_version marker; the local embedder already reports itself unavailable when fastembed is absent.
Parsed by hand: `packaging` is not one of empirica's dependencies, and the clean-environment suite has only those.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _api() -> dict[str, str]:
    """Requirement name -> its marker ('' for none)."""
    out: dict[str, str] = {}
    for req in tomllib.loads(_PYPROJECT.read_text())["project"]["optional-dependencies"]["api"]:
        spec, _, marker = req.partition(";")
        name = spec.split(">")[0].split("=")[0].split("<")[0].split("!")[0].split("[")[0].strip()
        out[name] = marker.strip()
    return out


def test_fastembed_is_left_out_on_python_315():
    assert _api()["fastembed"] == "python_version < '3.15'"


def test_the_rest_of_the_extra_carries_no_marker():
    assert {name: m for name, m in _api().items() if name != "fastembed" and m} == {}


def test_the_embedder_reports_itself_unavailable_without_fastembed(monkeypatch):
    import importlib.util

    from empirica.local_server import embedder

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "fastembed" else real(name, *a))
    assert embedder.available() is False


def test_the_python_floor_covers_every_dependency_floor():
    """2026-11-19: soupsieve>=4.0 (a security floor) needs Python 3.11.5, while requires-python said >=3.10, so on 3.10 and
    3.11.0-3.11.4 the requirements were unsatisfiable and pip silently installed 1.16.1. The floor must be at least that."""
    project = tomllib.loads(_PYPROJECT.read_text())["project"]
    floor = tuple(int(x) for x in project["requires-python"].removeprefix(">=").split("."))
    assert floor >= (3, 11, 5)
    assert any(d.startswith("soupsieve>=4") for d in project["dependencies"])  # the floor's reason; revisit if it goes

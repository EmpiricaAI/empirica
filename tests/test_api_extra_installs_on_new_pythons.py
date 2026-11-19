"""empirica[api] stays installable on a Python that fastembed's onnxruntime has no wheel for (autonomy, 2026-11-19).

On Python 3.15 the extra did not resolve at all, and a resolver backtracked a seat to 1.15.1 to satisfy it. fastembed is
gated behind a python_version marker; the local embedder already reports itself unavailable when fastembed is absent.
"""

from __future__ import annotations

from pathlib import Path

import tomllib
from packaging.requirements import Requirement

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _api() -> dict[str, Requirement]:
    reqs = [Requirement(r) for r in tomllib.loads(_PYPROJECT.read_text())["project"]["optional-dependencies"]["api"]]
    return {r.name: r for r in reqs}


def test_fastembed_is_left_out_on_python_315():
    fastembed = _api()["fastembed"]
    assert fastembed.marker is not None
    assert fastembed.marker.evaluate({"python_version": "3.15"}) is False
    assert fastembed.marker.evaluate({"python_version": "3.14"}) is True


def test_the_rest_of_the_extra_carries_no_such_marker():
    for name, req in _api().items():
        if name != "fastembed":
            assert req.marker is None, name


def test_the_embedder_reports_itself_unavailable_without_fastembed(monkeypatch):
    import importlib.util

    from empirica.local_server import embedder

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "fastembed" else real(name, *a))
    assert embedder.available() is False

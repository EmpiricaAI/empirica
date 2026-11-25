"""search_trajectories warns before its removal in 1.18.0 (goal 95c3ee16): nothing calls it, but it is documented."""

from __future__ import annotations

import pytest

from empirica.core import epistemic_trajectory as et


def test_calling_it_warns(monkeypatch):
    import empirica.core.qdrant.vector_store as vs

    monkeypatch.setattr(vs, "search_epistemics", lambda *a, **k: [])
    with pytest.warns(DeprecationWarning, match="1.18.0"):
        et.search_trajectories("pid", "q")

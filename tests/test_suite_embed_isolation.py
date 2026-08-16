"""The suite must not measure the box: embeddings are disabled suite-wide.

Root cause this guards (2026-08-16): tests isolated the SQL side
(EMPIRICA_SESSION_DB → tmp) but not Qdrant, so subprocess CLI calls embedded
goals into the LIVE per-project collections — 38 orphan test goals reached 44%
of the goals collection and polluted every PREFLIGHT/CHECK retrieval.

These tests guard the GUARD. If conftest's isolate_empirica_instance stops
setting EMPIRICA_ENABLE_EMBEDDINGS=false, they fail loudly instead of the leak
returning silently.
"""

from __future__ import annotations

import os


def test_suite_env_disables_embeddings():
    """The conftest session fixture must have set the kill switch — and because
    subprocess helpers build env via dict(os.environ), CLI subprocesses inherit
    it too (the exact path the 38-point leak used)."""
    assert os.environ.get("EMPIRICA_ENABLE_EMBEDDINGS") == "false"


def test_qdrant_unavailable_under_suite_env(monkeypatch):
    """_check_qdrant_available honors the flag on a fresh cache."""
    import empirica.core.qdrant.connection as qc

    monkeypatch.setattr(qc, "_qdrant_available", None)  # reset per-process cache
    assert qc._check_qdrant_available() is False


def test_embed_goal_short_circuits_without_client(monkeypatch):
    """embed_goal must return False before ever constructing a client.

    Positive control for the absence claim: a client factory that explodes —
    if the embed path reached it, the test would error rather than pass.
    """
    import empirica.core.qdrant.connection as qc
    import empirica.core.qdrant.goals as qg

    monkeypatch.setattr(qc, "_qdrant_available", None)

    def _boom():  # pragma: no cover - reaching this IS the failure
        raise AssertionError("embed path reached the Qdrant client despite the suite guard")

    monkeypatch.setattr(qg, "_get_qdrant_client", _boom)
    assert qg.embed_goal("proj", "gid", "objective") is False

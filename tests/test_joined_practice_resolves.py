"""A practice joined as a member resolves its address (goal: mesh-support prop_rq6jmex2).

Cortex lists a project only under its owner on the roster; a member who joined it (ec31d69bd) finds its address as
ai_id_canonical on GET /v1/users/me/projects (the owner's 3-form, cortex f578439c). The listener and `mesh diagnose` read
only the caller's tenant on the roster, so a joined member's listener never subscribed.
"""

from __future__ import annotations

import io
import json

from empirica.core.loop_scheduler import content_poll as cp

ROSTER = {"self": {"tenant_slug": "francisco"}, "org": {"tenants": [{"tenant_slug": "francisco", "projects": []}]}}
PROJECTS = {"projects": [{"slug": "empirica-consult", "ai_id_canonical": "empirica-consulting.david.empirica-consult"}]}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake(monkeypatch, projects=PROJECTS):
    def open_url(req, timeout=None):
        body = ROSTER if req.full_url.endswith("/roster") else projects
        return _Resp(json.dumps(body).encode())

    monkeypatch.setattr(cp, "open_url", open_url)
    cp._CANONICAL_AI_ID_CACHE.clear()
    cp._CANONICAL_FAILED_AT.clear()


def test_the_listener_resolves_a_joined_practice_to_the_owners_address(monkeypatch):
    _fake(monkeypatch)
    assert (
        cp._resolve_canonical_ai_id("https://c", "k", "empirica-consult")
        == "empirica-consulting.david.empirica-consult"
    )


def test_a_practice_neither_owned_nor_joined_still_misses(monkeypatch):
    _fake(monkeypatch, projects={"projects": []})
    assert cp._resolve_canonical_ai_id("https://c", "k", "empirica-consult") == "empirica-consult"


def test_a_non_canonical_value_is_not_taken(monkeypatch):
    _fake(monkeypatch, projects={"projects": [{"slug": "empirica-consult", "ai_id_canonical": None}]})
    assert cp.joined_practice_canonical("https://c", "k", "empirica-consult") is None


def test_diagnose_passes_a_joined_practice(monkeypatch):
    from empirica.cli.command_handlers import _mesh_diagnose_cortex as d

    monkeypatch.setattr(d, "_http_get_json", lambda url, **k: ROSTER)
    monkeypatch.setattr(cp, "joined_practice_canonical", lambda *a, **k: "empirica-consulting.david.empirica-consult")
    result = d.check_identity("empirica-consult", "https://c", "k")
    assert result.status == "pass" and "empirica-consulting.david.empirica-consult" in result.message

"""The write tier: what core's client does to a server, and what must come back.

Opt-in (``--allow-writes``) and for DISPOSABLE servers only. Everything it creates it removes: proposals are archived,
the project it registers is unregistered, the auto-accept setting is put back.
"""
from __future__ import annotations

import uuid

import pytest


def needs(server, capability):
    if capability not in server["capabilities"]:
        pytest.skip(f"server lacks {capability}")


def body_of(resp):
    return resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}


def succeeded(resp):
    """A 2xx whose body does not say it failed (the client treats ``ok: false`` or an ``error`` as failure)."""
    return 200 <= resp.status_code < 300 and body_of(resp).get("ok") is not False and not body_of(resp).get("error")


class Mail:
    """Self-addressed proposals, archived again when the test ends."""

    def __init__(self, http, auth, token, address):
        self.http, self.auth, self.token, self.address = http, auth, token, address
        self.sent: list[str] = []

    def body(self, key=None, **over):
        marker = uuid.uuid4().hex[:12]
        body = {
            "type": "collab_brief", "title": f"conformance {marker}",
            "summary": "contract conformance probe, safe to archive",
            "source_claude": self.address, "target_claudes": [self.address],
            "payload": {"idempotency_key": key or f"conf-{marker}"},
        }
        body.update(over)
        return body

    def post(self, body):
        resp = self.http.post("/v1/orchestration/propose", headers=self.auth, json=body)
        if succeeded(resp) and body_of(resp).get("proposal_id"):
            self.sent.append(body_of(resp)["proposal_id"])
        return resp

    def archive(self, pid, reason="conformance cleanup"):
        return self.http.post(f"/v1/orchestration/{pid}/archive", headers=self.auth,
                              json={"reason": reason, "ai_id": self.address})

    def complete(self, pid, result="shipped"):
        return self.http.post(f"/v1/orchestration/{pid}/complete", headers=self.auth,
                              json={"result": result, "note": "conformance",
                                    "ai_id": self.address})


@pytest.fixture
def mail(http, auth, token, me, server):
    needs(server, "orchestration.mailbox")
    m = Mail(http, auth, token, me["address"])
    yield m
    for pid in dict.fromkeys(m.sent):
        m.archive(pid)


def inbox_ids(http, auth, address):
    resp = http.get("/v1/orchestration/inbox", headers=auth, params={"ai_id": address, "limit": 200})
    assert resp.status_code == 200, resp.text[:200]
    return [p["id"] for p in resp.json()["proposals"]]


# ── propose ───────────────────────────────────────────────────────────────────────────────────────────────

def test_propose_returns_a_proposal_id_that_can_be_fetched(writes, http, auth, mail):
    resp = mail.post(mail.body())
    assert succeeded(resp), resp.text[:300]
    pid = body_of(resp)["proposal_id"]
    assert http.get(f"/v1/orchestration/{pid}", headers=auth).status_code == 200


def test_the_same_idempotency_key_sent_twice_is_one_proposal(writes, http, auth, me, mail):
    """The client retries a propose that got no answer, with the same key: the retry must not add a second row."""
    body = mail.body(key=f"conf-{uuid.uuid4().hex}")
    first, second = mail.post(body), mail.post(body)
    assert succeeded(first), first.text[:300]
    assert succeeded(second), second.text[:300]
    assert body_of(second)["proposal_id"] == body_of(first)["proposal_id"]
    assert inbox_ids(http, auth, me["address"]).count(body_of(first)["proposal_id"]) == 1


def test_a_different_body_under_the_same_key_is_refused_not_merged(writes, mail):
    key = f"conf-{uuid.uuid4().hex}"
    first = mail.post(mail.body(key=key))
    other = mail.post(mail.body(key=key, summary="a different proposal"))
    assert succeeded(first)
    assert not succeeded(other), "a changed body under a used key must not be accepted as the same proposal"


# ── complete and archive ──────────────────────────────────────────────────────────────────────────────────

def test_a_proposal_can_be_completed_by_its_target(writes, mail):
    pid = body_of(mail.post(mail.body()))["proposal_id"]
    resp = mail.complete(pid)
    assert succeeded(resp), resp.text[:300]


def test_a_proposal_can_be_archived_and_leaves_the_inbox(writes, http, auth, me, mail):
    pid = body_of(mail.post(mail.body()))["proposal_id"]
    resp = mail.archive(pid)
    assert succeeded(resp) and not body_of(resp).get("no_op"), resp.text[:300]
    assert pid not in inbox_ids(http, auth, me["address"])


def test_archive_and_complete_of_an_unknown_proposal_never_report_success(writes, mail):
    """``no_op`` / 404 / ``ok: false`` are all honest; a clean success for an id that does not exist is not."""
    ghost = "prop_conformance_does_not_exist"
    for resp in (mail.archive(ghost), mail.complete(ghost)):
        assert resp.status_code == 404 or body_of(resp).get("no_op") or not succeeded(resp), resp.text[:300]


# ── identity-crossing propose ─────────────────────────────────────────────────────────────────────────────

def test_a_propose_to_another_tenant_is_501_with_the_capability_on_a_server_without_it(writes, server, mail):
    if "orchestration.cross_tenant" in server["capabilities"]:
        pytest.skip("the server has the capability; the membrane decides, and a real cross-tenant send is not a probe")
    resp = mail.post(mail.body(target_claudes=["other.nobody.practice"]))
    assert resp.status_code == 501 and body_of(resp).get("capability") == "orchestration.cross_tenant"


# ── projects ──────────────────────────────────────────────────────────────────────────────────────────────

def test_a_project_can_be_registered_and_unregistered(writes, http, auth, server):
    needs(server, "projects.registry")
    name = f"conformance-{uuid.uuid4().hex[:10]}"
    reg = http.post("/v1/projects/register", headers=auth, json={"name": name, "display_name": name})
    assert reg.status_code in (200, 201), reg.text[:300]
    pid = body_of(reg).get("project_id") or body_of(reg).get("id")
    try:
        assert pid, "the register answer names no project id"
        listed = http.get("/v1/users/me/projects", headers=auth).json()["projects"]
        assert any(p.get("id") == pid or p.get("project_id") == pid for p in listed)
    finally:
        if pid:
            gone = http.post("/v1/projects/unregister", headers=auth, json={"project_id": pid, "purge": True})
            assert gone.status_code in (200, 201, 204), gone.text[:300]


def test_a_project_registered_with_its_own_id_keeps_that_id(writes, http, auth, server):
    """The upgrade path re-registers a local practice under the project id its machine already uses; a server
    that minted a fresh id would orphan the practice's history."""
    needs(server, "projects.registry")
    own_id = str(uuid.uuid4())
    name = f"conformance-{uuid.uuid4().hex[:10]}"
    reg = http.post("/v1/projects/register", headers=auth, json={"name": name, "display_name": name, "project_id": own_id})
    assert reg.status_code in (200, 201), reg.text[:300]
    got = body_of(reg).get("project_id") or body_of(reg).get("id")
    try:
        assert got == own_id, f"asked to register under {own_id}, the server answered {got}"
    finally:
        for pid in {got, own_id} - {None}:  # a server that minted its own id must not leave a project behind
            http.post("/v1/projects/unregister", headers=auth, json={"project_id": pid, "purge": True})


# ── artifact import (what `empirica mesh connect` relies on) ───────────────────────────────────────────────
#
# Dry run only: it reads what the server would do and writes nothing, so these cases need no cleanup beyond the
# project they register. The response FIELDS and their TYPES are the contract: a client that reads `created` as a
# count, or `refused` as a number, breaks quietly.

def _import_nodes():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    nodes = [
        {"ref": "f1", "type": "finding", "id": a, "created_at": 1_780_000_000.5, "is_resolved": True,
         "data": {"finding": "conformance probe finding", "impact": 0.5, "visibility": "project"}},
        {"ref": "u1", "type": "unknown", "id": b, "created_at": 1_780_000_001.0,
         "data": {"unknown": "conformance probe unknown", "visibility": "org"}},
    ]
    return nodes, [{"from": "u1", "to": "f1", "relation": "evidence"}], {a, b}


def _registered(http, auth):
    own_id = str(uuid.uuid4())
    name = f"conformance-{uuid.uuid4().hex[:10]}"
    reg = http.post("/v1/projects/register", headers=auth, json={"name": name, "display_name": name, "project_id": own_id})
    assert reg.status_code in (200, 201), reg.text[:300]
    return own_id


def test_an_import_dry_run_reports_what_it_would_create_and_writes_nothing(writes, http, auth, server):
    needs(server, "artifacts.import")
    pid = _registered(http, auth)
    try:
        nodes, edges, ids = _import_nodes()
        resp = http.post("/v1/artifacts/import", headers=auth, json={
            "project_id": pid, "nodes": nodes, "edges": edges, "dry_run": True,
            "source": {"machine": "conformance", "practice": "conformance", "exported_at": 1_780_000_100.0}})
        body = body_of(resp)
        assert resp.status_code == 200 and body.get("ok") is True, resp.text[:400]
        assert body["dry_run"] is True and body["project_id"] == pid
        assert isinstance(body["would_create"], list) and {w["id"] for w in body["would_create"]} == ids
        assert all(set(w) >= {"ref", "id"} for w in body["would_create"])
        assert body["created"] == {} and body["nodes_created"] == 0, "a dry run must not create anything"
        for field in ("skipped_existing", "refused", "errors"):
            assert isinstance(body[field], list), f"{field} must be a list, got {type(body[field]).__name__}"
        assert isinstance(body["edges_wired"], int)
    finally:
        http.post("/v1/projects/unregister", headers=auth, json={"project_id": pid, "purge": True})


def test_an_import_without_a_registered_project_is_refused_not_guessed(writes, http, auth, server):
    needs(server, "artifacts.import")
    nodes, _, _ = _import_nodes()
    assert http.post("/v1/artifacts/import", headers=auth, json={"nodes": nodes}).status_code == 400
    unknown = http.post("/v1/artifacts/import", headers=auth, json={"project_id": str(uuid.uuid4()), "nodes": nodes})
    assert unknown.status_code == 404


def test_an_import_refuses_a_batch_over_the_node_cap_naming_it(writes, http, auth, server):
    needs(server, "artifacts.import")
    pid = _registered(http, auth)
    try:
        nodes = [{"ref": f"n{i}", "type": "finding", "id": str(uuid.uuid4()),
                  "data": {"finding": f"probe {i}", "impact": 0.1}} for i in range(501)]
        resp = http.post("/v1/artifacts/import", headers=auth, json={"project_id": pid, "nodes": nodes, "dry_run": True})
        assert resp.status_code == 400 and "500" in resp.text
    finally:
        http.post("/v1/projects/unregister", headers=auth, json={"project_id": pid, "purge": True})


def test_an_edge_relation_outside_the_served_set_refuses_the_whole_batch(writes, http, auth, server):
    """`related` is the generic relation a local graph may hold; the server serves ten others and a batch carrying an
    unknown one is a 400 naming it. A client filters before it sends."""
    needs(server, "artifacts.import")
    pid = _registered(http, auth)
    try:
        nodes, _, _ = _import_nodes()
        edges = [{"from": "u1", "to": "f1", "relation": "related"}]
        resp = http.post("/v1/artifacts/import", headers=auth, json={"project_id": pid, "nodes": nodes, "edges": edges, "dry_run": True})
        assert resp.status_code == 400 and "related" in resp.text
    finally:
        http.post("/v1/projects/unregister", headers=auth, json={"project_id": pid, "purge": True})


def test_is_resolved_on_a_type_that_has_no_resolved_state_refuses_the_whole_batch(writes, http, auth, server):
    """Findings, unknowns, dead ends and mistakes close with is_resolved; an assumption carries its state in
    data.status and a decision has none. Sending it anyway is a 400 for the batch, not a quiet drop."""
    needs(server, "artifacts.import")
    pid = _registered(http, auth)
    try:
        nodes = [{"ref": "a1", "type": "assumption", "id": str(uuid.uuid4()), "is_resolved": True,
                  "data": {"assumption": "conformance probe", "confidence": 0.5, "domain": "x"}}]
        resp = http.post("/v1/artifacts/import", headers=auth, json={"project_id": pid, "nodes": nodes, "dry_run": True})
        assert resp.status_code == 400 and "is_resolved" in resp.text
    finally:
        http.post("/v1/projects/unregister", headers=auth, json={"project_id": pid, "purge": True})


# ── auto-accept ───────────────────────────────────────────────────────────────────────────────────────────

def test_auto_accept_can_be_set_and_read_back_and_is_restored(writes, http, auth, server):
    needs(server, "orchestration.auto_accept")
    before = http.get("/v1/users/me/auto-accept", headers=auth).json()["enabled"]
    try:
        for want in (not before, before):
            put = http.post("/v1/users/me/auto-accept", headers=auth, json={"enabled": want})
            assert put.status_code == 200 and put.json()["enabled"] is want, put.text[:200]
            assert http.get("/v1/users/me/auto-accept", headers=auth).json()["enabled"] is want
    finally:
        http.post("/v1/users/me/auto-accept", headers=auth, json={"enabled": before})

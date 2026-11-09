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
            "api_key": self.token, "type": "collab_brief", "title": f"conformance {marker}",
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
                              json={"api_key": self.token, "reason": reason, "ai_id": self.address})

    def complete(self, pid, result="shipped"):
        return self.http.post(f"/v1/orchestration/{pid}/complete", headers=self.auth,
                              json={"api_key": self.token, "result": result, "note": "conformance",
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

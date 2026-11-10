"""empirica-local's group-A routes (spec phase 1.3, goal 92fe2c88): one person, addresses ``local.<user>.<practice>``.

Served from ``empirica serve`` on a loopback bind; every proposal lands accepted. A target outside ``local.<user>.``
answers 501 with ``orchestration.cross_tenant``: the boundary is in the target, not the route.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

pytest.importorskip("fastapi")

ME = "local.tester"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("EMPIRICA_SERVE_HOST", "127.0.0.1")
    monkeypatch.setenv("EMPIRICA_LOCAL_USER", "tester")
    monkeypatch.delenv("EMPIRICA_LOCAL_TOKEN", raising=False)
    monkeypatch.delenv("EMPIRICA_LOCAL_DB", raising=False)
    from empirica.api.serve_app import create_serve_app

    return TestClient(create_serve_app(), base_url="http://127.0.0.1:8000")


def _register(client, name):
    resp = client.post("/v1/projects/register", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["project_id"]


def _send(client, title="t", targets=(f"{ME}.b",), source=f"{ME}.a", key=None, **over):
    body = {
        "type": "collab_brief",
        "title": title,
        "summary": "s",
        "source_claude": source,
        "target_claudes": list(targets),
        "payload": {"idempotency_key": key} if key else {},
    }
    body.update(over)
    return client.post("/v1/orchestration/propose", json=body)


def test_identity_is_the_local_namespace(client):
    me = client.get("/v1/users/me").json()
    assert me["org_id"] == "local" and me["tenant_slug"] == "tester" and me["mesh_id_prefix"] == ME


def test_a_registered_practice_is_in_the_roster_with_its_canonical_address(client):
    pid = _register(client, "alpha")
    roster = client.get("/v1/users/me/roster").json()
    assert roster["self"]["tenant_slug"] == "tester" and roster["org"]["slug"] == "local"
    (tenant,) = roster["org"]["tenants"]
    assert {"id": pid, "slug": "alpha", "ai_id_short": "alpha", "ai_id_mesh": f"{ME}.alpha"}.items() <= tenant[
        "projects"
    ][0].items()
    assert client.get("/v1/projects/by-slug/alpha").json()["project"]["id"] == pid
    listed = client.get("/v1/users/me/projects").json()["projects"]
    assert listed[0]["ai_id_canonical"] == f"{ME}.alpha"


def test_the_workspace_registry_feeds_the_roster_by_directory_name(client, tmp_path):
    ws = tmp_path / ".empirica" / "workspace"
    ws.mkdir(parents=True)
    with sqlite3.connect(ws / "workspace.db") as db:
        db.execute("CREATE TABLE global_projects (id TEXT, name TEXT, trajectory_path TEXT, status TEXT)")
        db.execute("INSERT INTO global_projects VALUES ('u1', 'Pretty Name', '/x/beta-practice/.empirica', 'active')")
        db.execute("INSERT INTO global_projects VALUES ('u2', 'Gone', '/x/gone/.empirica', 'archived')")
    slugs = [p["slug"] for p in client.get("/v1/users/me/roster").json()["org"]["tenants"][0]["projects"]]
    assert slugs == ["beta-practice"]


def test_registering_twice_keeps_one_id_and_unregister_removes_it(client):
    first = _register(client, "alpha")
    assert _register(client, "alpha") == first
    assert client.post("/v1/projects/unregister", json={"slug": "alpha"}).status_code == 200
    assert client.get("/v1/projects/by-slug/alpha").status_code == 404


def test_propose_then_read_from_both_mailboxes(client):
    pid = _send(client).json()["proposal_id"]
    inbox = client.get("/v1/orchestration/inbox", params={"ai_id": f"{ME}.b"}).json()
    assert [p["id"] for p in inbox["proposals"]] == [pid] and inbox["proposals"][0]["status"] == "accepted"
    assert inbox["returned"] == inbox["matched"] == 1 and inbox["has_more"] is False
    outbox = client.get("/v1/orchestration/outbox", params={"ai_id": f"{ME}.a"}).json()
    assert [p["id"] for p in outbox["proposals"]] == [pid]
    assert client.get(f"/v1/orchestration/{pid}").json()["id"] == pid
    assert client.get("/v1/orchestration/prop_nope").status_code == 404


def test_a_bare_name_in_the_mailbox_query_returns_nothing(client):
    _send(client)
    assert client.get("/v1/orchestration/inbox", params={"ai_id": "b"}).json()["proposals"] == []


def test_limit_and_status_filters_report_the_page_honestly(client):
    for i in range(3):
        _send(client, title=f"t{i}")
    page = client.get("/v1/orchestration/inbox", params={"ai_id": f"{ME}.b", "limit": 2}).json()
    assert page["returned"] == 2 and page["matched"] == 3 and page["has_more"] is True
    none = client.get("/v1/orchestration/inbox", params={"ai_id": f"{ME}.b", "status": "completed"}).json()
    assert none["proposals"] == []


def test_idempotent_replay_and_conflict(client):
    a = _send(client, key="k").json()
    b = _send(client, key="k").json()
    assert a["proposal_id"] == b["proposal_id"] and b.get("replay_caught") is True
    clash = _send(client, key="k", summary="other")
    assert clash.status_code == 409 and clash.json()["error"] == "idempotency_key_conflict"


def test_a_target_outside_the_local_namespace_is_501_cross_tenant(client):
    resp = _send(client, targets=("other.nobody.practice",))
    assert resp.status_code == 501 and resp.json() == {"capability": "orchestration.cross_tenant"}


def test_an_invalid_proposal_is_400(client):
    assert _send(client, type="not_a_type").status_code == 400
    assert _send(client, targets=()).status_code == 400


def test_complete_and_archive(client):
    pid = _send(client).json()["proposal_id"]
    done = client.post(f"/v1/orchestration/{pid}/complete", json={"result": "shipped", "note": "n", "ai_id": f"{ME}.b"})
    assert done.status_code == 200 and done.json()["ok"] is True
    assert client.get(f"/v1/orchestration/{pid}").json()["status"] == "completed"
    arch = client.post(f"/v1/orchestration/{pid}/archive", json={"reason": "done", "ai_id": f"{ME}.b"}).json()
    assert arch["ok"] is True and not arch.get("no_op")
    assert client.get("/v1/orchestration/inbox", params={"ai_id": f"{ME}.b"}).json()["proposals"] == []
    again = client.post(f"/v1/orchestration/{pid}/archive", json={"reason": "done", "ai_id": f"{ME}.b"}).json()
    assert again["no_op"] is True


def test_unknown_ids_never_report_success(client):
    assert client.post("/v1/orchestration/prop_x/complete", json={"result": "shipped"}).status_code == 404
    assert client.post("/v1/orchestration/prop_x/archive", json={"reason": "r"}).status_code == 404


def test_archive_needs_a_reason_and_complete_a_known_result(client):
    pid = _send(client).json()["proposal_id"]
    assert client.post(f"/v1/orchestration/{pid}/archive", json={"reason": ""}).status_code == 400
    assert client.post(f"/v1/orchestration/{pid}/complete", json={"result": "maybe"}).status_code == 400


def test_heartbeats_and_system_events_are_accepted(client):
    assert client.post("/v1/listeners/heartbeat", json={"ai_id": f"{ME}.a", "instance_id": "h"}).json()["ok"]
    beat = {"machine": "m", "session_id": "s", "status": "active"}
    assert client.post("/v1/practitioners/heartbeat", json=beat).status_code == 200
    assert client.post("/v1/practitioners/heartbeat", json={}).status_code == 400
    assert client.post("/v1/system/event", json={"category": "c", "title": "t"}).status_code == 200


def test_a_configured_token_is_required(client, monkeypatch):
    monkeypatch.setenv("EMPIRICA_LOCAL_TOKEN", "sekrit")
    assert client.get("/v1/users/me").status_code == 401
    assert client.get("/v1/users/me", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/v1/users/me", headers={"Authorization": "Bearer sekrit"}).status_code == 200
    assert client.get("/v1/health").status_code == 200


# ── wake (phase 1.4): core's own proposal_event lines in ~/.empirica/loop_fires.log, which the session Monitor tails ──


def _fires(tmp_path):
    log = tmp_path / ".empirica" / "loop_fires.log"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_a_proposal_wakes_each_target_and_a_completion_wakes_the_source(client, tmp_path):
    pid = _send(client, targets=(f"{ME}.b", f"{ME}.c"), key="w").json()["proposal_id"]
    inbox = [(e["instance_id"], e["direction"], e["status"]) for e in _fires(tmp_path)]
    assert inbox == [("b", "inbox", "accepted"), ("c", "inbox", "accepted")]
    assert all(e["event_type"] == "proposal_event" and e["proposal_id"] == pid for e in _fires(tmp_path))
    _send(client, targets=(f"{ME}.b", f"{ME}.c"), key="w")  # a replay wakes nobody
    assert len(_fires(tmp_path)) == 2
    body = {"result": "shipped", "commit_sha": "abc1234"}
    client.post(f"/v1/orchestration/{pid}/complete", json={**body, "ai_id": f"{ME}.b"})
    assert len(_fires(tmp_path)) == 2  # one of two targets: the proposal is still open
    client.post(f"/v1/orchestration/{pid}/complete", json={**body, "ai_id": f"{ME}.c"})
    last = _fires(tmp_path)[-1]
    assert (last["instance_id"], last["direction"], last["status"]) == ("a", "outbox", "completed")


def test_a_wake_line_that_cannot_be_written_is_counted_in_the_capability_document(client, tmp_path, monkeypatch):
    from empirica.local_server import wake

    monkeypatch.setitem(wake.FAILURES, "count", 0)
    (tmp_path / ".empirica" / "loop_fires.log").mkdir(parents=True)  # a directory where the log should be
    assert _send(client).status_code == 200  # the proposal is still stored
    health = client.get("/v1/health").json()["wake"]
    assert health["failures"] == 1 and health["last_error"]


def test_mesh_sharing_agreements_are_an_empty_list_not_501(client):
    resp = client.get("/v1/orgs/me/mesh_sharing_agreements")
    assert resp.status_code == 200 and resp.json() == {"agreements": []}


# ── the 1.15.0 sweep: the browser on this machine, completion rights, repeat wakes ─────────────────────────────────────


def test_a_web_page_cannot_post_into_the_mailbox(client, tmp_path):
    """A text/plain POST is a 'simple' request: a page on any site can send it with no preflight. Loopback stops other
    machines, not the browser here."""
    body = '{"type":"collab_brief","title":"t","summary":"s","source_claude":"local.tester.a","target_claudes":["local.tester.b"]}'
    sneaky = client.post("/v1/orchestration/propose", content=body, headers={"Content-Type": "text/plain"})
    assert sneaky.status_code == 415
    foreign = client.post(
        "/v1/orchestration/propose", content=body,
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
    )  # fmt: skip
    assert foreign.status_code == 403
    assert client.get("/v1/orchestration/inbox", params={"ai_id": f"{ME}.b"}).json()["proposals"] == []
    assert _fires(tmp_path) == []


def test_a_rebound_hostname_cannot_read_the_mailbox(client):
    """DNS rebinding: a page whose name now resolves to 127.0.0.1 is same-origin to itself; its Host header gives it away."""
    _send(client)
    resp = client.get("/v1/orchestration/inbox", params={"ai_id": f"{ME}.b"}, headers={"Host": "evil.example:8000"})
    assert resp.status_code == 403


def test_a_loopback_origin_is_allowed(client):
    resp = client.post(
        "/v1/orchestration/propose",
        json={"type": "collab_brief", "title": "t", "summary": "s", "source_claude": f"{ME}.a", "target_claudes": [f"{ME}.b"]},
        headers={"Origin": "http://localhost:3000"},
    )  # fmt: skip
    assert resp.status_code == 200


def test_a_finished_proposal_cannot_be_completed_again(client, tmp_path):
    pid = _send(client).json()["proposal_id"]
    assert (
        client.post(f"/v1/orchestration/{pid}/complete", json={"result": "shipped", "ai_id": f"{ME}.b"}).status_code
        == 200
    )
    wakes = len(_fires(tmp_path))
    again = client.post(f"/v1/orchestration/{pid}/complete", json={"result": "failed", "ai_id": f"{ME}.b"})
    assert again.status_code == 200 and again.json()["note"] == "already_completed"
    assert client.get(f"/v1/orchestration/{pid}").json()["status"] == "completed"
    assert len(_fires(tmp_path)) == wakes  # no second wake


def test_only_a_target_can_complete(client):
    pid = _send(client).json()["proposal_id"]
    resp = client.post(f"/v1/orchestration/{pid}/complete", json={"result": "shipped", "ai_id": f"{ME}.zzz"})
    assert resp.status_code == 403
    assert client.get(f"/v1/orchestration/{pid}").json()["status"] == "accepted"


def test_a_repeated_ack_on_a_finished_multi_target_proposal_does_not_wake_again(client, tmp_path):
    pid = _send(client, targets=(f"{ME}.b", f"{ME}.c")).json()["proposal_id"]
    for t in ("b", "c"):
        client.post(f"/v1/orchestration/{pid}/complete", json={"result": "shipped", "ai_id": f"{ME}.{t}"})
    wakes = len(_fires(tmp_path))
    client.post(f"/v1/orchestration/{pid}/complete", json={"result": "shipped", "ai_id": f"{ME}.c"})
    assert len(_fires(tmp_path)) == wakes


def test_archive_is_only_for_a_target_or_the_source(client):
    pid = _send(client).json()["proposal_id"]
    resp = client.post(f"/v1/orchestration/{pid}/archive", json={"reason": "r", "ai_id": "anything.at.all"})
    assert resp.status_code == 403
    ok = client.post(f"/v1/orchestration/{pid}/archive", json={"reason": "r", "ai_id": f"{ME}.a"})
    assert ok.status_code == 200 and ok.json()["no_op"] is False


def test_a_wake_error_of_any_kind_is_counted_not_raised(client, monkeypatch):
    from empirica.local_server import wake

    monkeypatch.setitem(wake.FAILURES, "count", 0)
    monkeypatch.setattr(wake, "build_event", lambda *a, **k: (_ for _ in ()).throw(KeyError("boom")))
    assert _send(client).status_code == 200
    assert wake.FAILURES["count"] == 1


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8000", "localhost:8000", "LOCALHOST:8000", "[::1]:8000", "[::ffff:127.0.0.1]:8000"]
)
def test_every_loopback_host_form_is_accepted(client, host):
    assert client.get("/v1/users/me", headers={"Host": host}).status_code == 200

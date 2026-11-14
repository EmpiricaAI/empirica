"""The read tier: what any contract-conforming server must answer, safely."""
from __future__ import annotations

import pytest

import re

from .shape import unsatisfied


def concrete(path):
    """A path with every ``{parameter}`` replaced by a placeholder value."""
    return re.sub(r"\{[^}]+\}", "x", path)


def _rows(contract, group):
    return contract[group]


def _get_rows(contract):
    return [r for r in _rows(contract, "group_a")
            if r["method"] == "GET" and r["auth"] == "bearer" and "{" not in r["path"]]


# ── health and the capability document ────────────────────────────────────────────────────────────────────

def test_health_needs_no_credential_and_carries_the_capability_document(http, contract):
    resp = http.get("/v1/health")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body.get("status") == "healthy"
    for key in ("edition", "contract", "capabilities"):
        assert key in body, f"/v1/health lacks '{key}'"


def test_the_server_speaks_the_contract_major_version_the_file_describes(server, contract):
    assert server["contract"].split(".")[0] == str(contract["contract"]).split(".")[0]


def test_every_advertised_capability_is_one_the_contract_names(server, contract):
    unknown = sorted(set(server["capabilities"]) - set(contract["capabilities"]))
    assert not unknown, f"capabilities the contract does not define: {unknown}"


# ── authentication ────────────────────────────────────────────────────────────────────────────────────────

def test_a_cortex_edition_refuses_every_bearer_route_without_a_credential(http, server, contract):
    if server["edition"] != "cortex":
        pytest.skip("only the cortex edition is required to authenticate; a local server may run with auth off")
    wrong = []
    for row in _rows(contract, "group_a"):
        if row["auth"] != "bearer":
            continue
        path = concrete(row["path"])
        resp = http.request(row["method"], path)          # no credential: must be refused before anything runs
        if resp.status_code != 401:
            wrong.append(f"{row['method']} {row['path']} -> {resp.status_code}")
    assert not wrong, wrong


# ── the GET routes core calls ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def answers(http, auth, contract, server):
    """One authenticated call per GET route that needs no path parameter, keyed by route id."""
    out = {}
    for row in _get_rows(contract):
        if row["capability"] and row["capability"] not in server["capabilities"]:
            continue
        params = {}
        if row["id"] in ("orchestration_inbox", "orchestration_outbox"):
            continue                                       # need the caller's own address first (below)
        out[row["id"]] = (row, http.get(row["path"], headers=auth, params=params))
    return out


def test_every_get_route_answers_json_with_the_fields_core_reads(answers):
    problems = []
    for rid, (row, resp) in answers.items():
        if resp.status_code != 200:
            problems.append(f"{rid}: {resp.status_code}")
            continue
        body = resp.json()
        for entry in row["response"]["required"]:
            gap = unsatisfied(body, entry)
            if gap:
                problems.append(f"{rid}: missing {gap}")
    assert not problems, problems


def _own_address(answers):
    roster = answers["users_me_roster"][1].json()
    mine = [t for t in roster["org"]["tenants"] if t["tenant_slug"] == roster["self"]["tenant_slug"]]
    projects = [p for t in mine for p in t.get("projects", [])]
    if not projects:
        pytest.skip("the caller has no practice in the roster, so there is no address to read a mailbox for")
    return projects[0]["ai_id_mesh"]


@pytest.fixture(scope="module")
def mailbox(http, auth, contract, answers, server):
    if "orchestration.mailbox" not in server["capabilities"]:
        pytest.skip("server lacks orchestration.mailbox")
    ai_id = _own_address(answers)
    out = {}
    for rid, path in (("orchestration_inbox", "/v1/orchestration/inbox"), ("orchestration_outbox", "/v1/orchestration/outbox")):
        out[rid] = http.get(path, headers=auth, params={"ai_id": ai_id, "limit": 5})
    return out


@pytest.mark.parametrize("rid", ["orchestration_inbox", "orchestration_outbox"])
def test_the_mailbox_lists_carry_the_fields_core_reads(mailbox, contract, rid):
    resp = mailbox[rid]
    assert resp.status_code == 200, resp.text[:200]
    row = next(r for r in contract["group_a"] if r["id"] == rid)
    problems = [g for e in row["response"]["required"] for g in [unsatisfied(resp.json(), e)] if g]
    assert not problems, problems


@pytest.mark.parametrize("rid", ["orchestration_inbox", "orchestration_outbox"])
def test_a_list_never_claims_to_be_whole_when_it_is_not(mailbox, rid):
    """``returned``/``matched``/``has_more``: when the server reports them, they must agree with each other.

    Absent keys make no claim. A ``matched`` smaller than ``returned`` is impossible; ``has_more: false`` with
    ``matched`` larger than ``returned`` says the page is complete while saying it is not.
    """
    body = mailbox[rid].json()
    returned, matched, more = body.get("returned"), body.get("matched"), body.get("has_more")
    if returned is not None:
        assert isinstance(returned, int) and returned == len(body["proposals"])
    if more is not None:
        assert isinstance(more, bool)
    if matched is not None and returned is not None:
        assert matched >= returned
        if more is False:
            assert matched == returned


def test_one_proposal_is_fetchable_by_id_and_an_unknown_id_is_404(http, auth, mailbox, server):
    first = mailbox["orchestration_inbox"].json()["proposals"]
    if first:
        got = http.get(f"/v1/orchestration/{first[0]['id']}", headers=auth)
        assert got.status_code == 200
        assert got.json().get("id") == first[0]["id"] or got.json().get("title")
    missing_resp = http.get("/v1/orchestration/prop_does_not_exist_0000", headers=auth)
    assert missing_resp.status_code == 404


def test_a_project_is_found_by_its_slug(http, auth, answers):
    projects = answers["users_me_projects_get"][1].json()["projects"]
    if not projects:
        pytest.skip("the caller has no registered project")
    slug = projects[0]["slug"]
    resp = http.get(f"/v1/projects/by-slug/{slug}", headers=auth)
    assert resp.status_code == 200


# ── the routes outside the cortex edition's reach: group B ────────────────────────────────────────────────

def test_group_b_routes_are_501_with_the_capability_on_a_server_without_it(http, auth, server, contract):
    wrong = []
    checked = 0
    for row in contract["group_b"]:
        if row["capability"] in server["capabilities"]:
            continue
        checked += 1
        path = concrete(row["path"])
        # A local server requires application/json on every POST (the browser-origin rule below), so send what a
        # real client sends: an unsent content type would be refused with 415 before the capability is checked.
        resp = http.request(row["method"], path, headers={**auth, "Content-Type": "application/json"},
                            content="{}" if row["method"] != "GET" else None)
        if row.get("local_status") != 501:
            continue                                       # the documented empty-list exception
        ok = resp.status_code == 501 and resp.json().get("capability") == row["capability"]
        if not ok:
            wrong.append(f"{row['method']} {row['path']} -> {resp.status_code}")
    if not checked:
        pytest.skip("the server has every capability: nothing is required to answer 501")
    assert not wrong, wrong


def test_group_b_routes_are_served_and_refuse_an_anonymous_caller_where_the_capability_exists(http, server, contract):
    if server["edition"] != "cortex":
        pytest.skip("anonymous refusal is the cortex edition's duty")
    wrong = []
    for row in contract["group_b"]:
        if row["capability"] not in server["capabilities"]:
            continue
        path = concrete(row["path"])
        resp = http.request(row["method"], path)
        if resp.status_code not in (401, 403):
            wrong.append(f"{row['method']} {row['path']} -> {resp.status_code}")
    assert not wrong, wrong


# ── a local server must not be drivable from a web page ───────────────────────────────────────────────────
#
# Core found (d0d8bbaf6) that a browser on the same machine could post into a local mailbox: a text/plain "simple"
# request needs no CORS preflight, and DNS rebinding makes a hostile page look same-origin. Every local route must
# therefore require a loopback Host, a loopback Origin when one is sent, and application/json on a POST. Cortex is not
# subject to this (it authenticates with a bearer header a browser does not send on its own), so the checks run only
# for a server whose edition is ``local``. The probe is an archive of a proposal that does not exist: it acts on nothing.

_PROBE = "/v1/orchestration/prop_conformance_csrf_probe/archive"
_REFUSED = (400, 403, 415, 421)


@pytest.fixture
def local_probe(http, token, server):
    """The probe needs no credential of its own: a local server may run with auth off (the README's own example),
    and a probe that skipped for want of a token would let an exposed server pass by being unconfigured."""
    if server["edition"] != "local":
        pytest.skip("browser-origin refusal is required of a local server; cortex authenticates with a bearer header")
    if "orchestration.mailbox" not in server["capabilities"]:
        pytest.skip("server lacks orchestration.mailbox")
    bearer = {"Authorization": f"Bearer {token}"} if token else {}

    def post(headers=None, content=None):
        h = {**bearer, "Content-Type": "application/json", **(headers or {})}
        return http.post(_PROBE, headers=h, content=content if content is not None else '{"reason": "probe"}')

    control = post()
    assert control.status_code not in _REFUSED, (
        f"the control request (JSON, loopback host, no origin) was refused with {control.status_code}: the refusal checks below would prove nothing")
    return post


def test_a_local_server_refuses_a_text_plain_post(local_probe):
    assert local_probe({"Content-Type": "text/plain"}).status_code in _REFUSED


# A look-alike is the case a prefix or substring check lets through (DNS rebinding uses exactly such names).
_FOREIGN_ORIGINS = ["http://evil.example", "http://127.0.0.1.evil.example", "http://localhost.evil.example", "null"]
_FOREIGN_HOSTS = ["evil.example", "127.0.0.1.evil.example", "localhost.evil.example"]


@pytest.mark.parametrize("origin", _FOREIGN_ORIGINS)
def test_a_local_server_refuses_a_foreign_origin(local_probe, origin):
    assert local_probe({"Origin": origin}).status_code in _REFUSED


@pytest.mark.parametrize("origin", ["http://127.0.0.1:1234", "http://localhost:1234"])
def test_a_local_server_accepts_a_loopback_origin(local_probe, origin):
    assert local_probe({"Origin": origin}).status_code not in _REFUSED


@pytest.mark.parametrize("host", _FOREIGN_HOSTS)
def test_a_local_server_refuses_a_foreign_host(local_probe, host):
    assert local_probe({"Host": host}).status_code in _REFUSED

"""Several teams' sign-ins on one machine (goal da0396f8).

A token is bound to one user in one org, so several teams means several token sets. `cortex.oauth` stays where it is, the
default team's set, because other tools read it directly (cortex-cli reads cortex.oauth.access_token); another team's set
lives at `cortex.orgs.<org_id>` with the same fields, and each block records its own org_id.
"""

from __future__ import annotations

import time

import pytest
import yaml

_AT_A, _AT_B, _RT, _NEW_B, _AT_A2 = "AT_A", "AT_B", "RT", "NEW_B", "AT_A2"  # fake fixtures, not credentials
_VALID = "valid"
_CORTEX = "https://cortex.getempirica.com"  # several teams need Empirica Cortex (David, 2026-11-16)
_AT_C, _NEW_A = "AT_C", "NEW_None"  # the default is renewed by its slot (None)


@pytest.fixture
def creds(tmp_path, monkeypatch):
    from empirica.config.credentials_loader import CredentialsLoader

    path = tmp_path / "credentials.yaml"
    monkeypatch.setenv("HOME", str(tmp_path))  # practices live under $HOME: the working-directory walk stops there
    for var in ("CORTEX_API_KEY", "CORTEX_URL", "CORTEX_REMOTE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EMPIRICA_CREDENTIALS_PATH", str(path))

    def write(cortex: dict) -> CredentialsLoader:
        path.write_text(yaml.safe_dump({"version": "1.0", "cortex": cortex}))
        CredentialsLoader._instance = None
        CredentialsLoader._credentials_cache = None
        return CredentialsLoader()

    yield write, path
    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None


def _set(token, org=None, **extra):
    return {"access_token": token, "refresh_token": _RT, "expires_at": time.time() + 3600,
            "token_endpoint": "https://c/t", "client_id": "cli", "refresh_owner": "cli",
            **({"org_id": org} if org else {}), **extra}  # fmt: skip


def test_no_org_asked_reads_the_default_block_as_before(creds):
    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    assert loader.get_cortex_oauth()["access_token"] == _AT_A
    assert loader.cortex_access_token() == _AT_A


def test_an_org_asked_reads_its_own_block(creds):
    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    assert loader.get_cortex_oauth(org_id="org-b")["access_token"] == _AT_B
    assert loader.get_cortex_oauth(org_id="org-a")["access_token"] == _AT_A  # the default is that org's block
    assert loader.cortex_access_token(org_id="org-b") == _AT_B


def test_an_org_with_no_sign_in_reads_as_none(creds):
    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a")})
    assert loader.get_cortex_oauth(org_id="org-c") == {}
    assert loader.cortex_access_token(org_id="org-c") is None


def test_a_default_signed_in_before_orgs_were_recorded_serves_any_org(creds):
    """Its org is unknown (pre-1.17.2 sign-in): it is offered, as before, rather than refusing every practice."""
    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A)})
    assert loader.get_cortex_oauth(org_id="org-a")["access_token"] == _AT_A


def test_every_sign_in_is_listed(creds):
    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    assert [(slot, block["access_token"]) for slot, block in loader.cortex_oauth_sets()] == [
        (None, _AT_A),
        ("org-b", _AT_B),
    ]


def test_a_refresh_of_one_org_writes_that_org_only(creds):
    write, path = creds
    expired = _set(_AT_B, "org-b", expires_at=time.time() - 60)
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": expired}})
    token = loader.cortex_access_token(
        org_id="org-b", refresh=lambda rt, ep: {"access_token": _NEW_B, "expires_at": time.time() + 3600}
    )
    assert token == _NEW_B
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["orgs"]["org-b"]["access_token"] == _NEW_B
    assert stored["oauth"]["access_token"] == _AT_A


def test_the_bearer_for_an_org_is_that_orgs_token(creds, monkeypatch):
    from empirica.core.auth import cortex_oauth as co

    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    assert co.cortex_bearer(loader)["bearer"] == _AT_A
    assert co.cortex_bearer(loader, org_id="org-b")["bearer"] == _AT_B


def test_an_org_with_no_sign_in_says_which_and_how(creds, monkeypatch):
    from empirica.core.auth import cortex_oauth as co

    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a")})
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    out = co.cortex_bearer(loader, org_id="org-c")
    assert out["bearer"] is None
    assert "org-c" in out["reason"] and "auth login --org org-c" in out["reason"]


def test_refresh_custody_is_read_from_the_orgs_own_block(creds, monkeypatch):
    from empirica.core.auth import cortex_oauth as co

    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a", refresh_owner="daemon"),
                    "orgs": {"org-b": _set(_AT_B, "org-b", refresh_owner="cli")}})  # fmt: skip
    assert co.custody_refresh(loader, org_id="org-a")[1] == "daemon"
    assert co.custody_refresh(loader, org_id="org-b")[1] == "cli"


# --- auth login writes the org's own block (it used to replace whatever team was stored) ---

_DISCO = {"issuer": "https://c", "authorization_endpoint": "https://c/v1/oauth/authorize",
          "token_endpoint": "https://c/t", "registration_endpoint": "https://c/v1/oauth/register"}  # fmt: skip


def _login(loader, monkeypatch, *, new_org, new_token, me_by_token=None, org=None):
    """Run login() over the --remote path with fakes: the new set acts in ``new_org``."""
    import urllib.parse

    from empirica.core.auth import cortex_oauth as co

    me = {new_token: {"org_id": new_org, "org_name": new_org.upper()}, **(me_by_token or {})}

    def http(url, *, data=None, headers=None, timeout=15.0, form=False):
        if "/.well-known" in url:
            return _DISCO
        if url.endswith("/register"):
            return {"client_id": "cli_new"}
        if url.endswith("/t"):
            if (data or {}).get("grant_type") == "refresh_token":
                raise RuntimeError("refresh refused")
            return {"access_token": new_token, "refresh_token": "RT_NEW", "expires_in": 3600}
        if url.endswith("/v1/users/me"):
            bearer = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
            if bearer in me:
                return me[bearer]
            raise RuntimeError("401")
        raise AssertionError(url)

    def paste(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        return f"code#{q['state']}"

    monkeypatch.setattr(co, "_serve_daemon_running", lambda: False)
    return co.login(loader=loader, http=http, remote=True, read_code=paste, org=org)


def test_a_first_sign_in_is_the_default_and_records_its_org(creds, monkeypatch):
    write, path = creds
    result = _login(write({"url": _CORTEX}), monkeypatch, new_org="org-a", new_token=_AT_A)
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["oauth"]["access_token"] == _AT_A and stored["oauth"]["org_id"] == "org-a"
    assert stored["oauth"]["org_name"] == "ORG-A" and "orgs" not in stored
    assert (result["slot"], result["org_id"]) == (None, "org-a")


def test_a_second_team_goes_beside_the_first_not_over_it(creds, monkeypatch):
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a")})
    result = _login(loader, monkeypatch, new_org="org-b", new_token=_AT_B, org="org-b")
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["oauth"]["access_token"] == _AT_A
    assert stored["orgs"]["org-b"]["access_token"] == _AT_B and stored["orgs"]["org-b"]["org_id"] == "org-b"
    assert result["slot"] == "org-b"
    assert stored["orgs"]["org-b"]["client_id"] == "cli_new"  # its own client, not the other team's family's


def test_signing_in_to_the_same_team_again_renews_the_default(creds, monkeypatch):
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a")})
    _login(loader, monkeypatch, new_org="org-a", new_token=_AT_A2)
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["oauth"]["access_token"] == _AT_A2 and "orgs" not in stored


def test_a_default_with_no_recorded_org_is_asked_which_it_is(creds, monkeypatch):
    """Signed in before orgs were recorded: one /v1/users/me with its own token says which team it is."""
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A)})
    _login(loader, monkeypatch, new_org="org-b", new_token=_AT_B, me_by_token={_AT_A: {"org_id": "org-a"}})
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["oauth"]["access_token"] == _AT_A and stored["oauth"]["org_id"] == "org-a"
    assert stored["orgs"]["org-b"]["access_token"] == _AT_B


def test_a_default_that_cannot_say_which_team_it_is_is_replaced(creds, monkeypatch):
    """Its token is dead and cannot be renewed: nothing usable is lost, so the new sign-in takes the default, as before."""
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, expires_at=time.time() - 60)})
    _login(loader, monkeypatch, new_org="org-b", new_token=_AT_B)
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["oauth"]["access_token"] == _AT_B and stored["oauth"]["org_id"] == "org-b"


def _run(handler, monkeypatch, loader, **kw):
    import json
    import types

    from empirica.cli.command_handlers import auth_commands

    monkeypatch.setattr(auth_commands, "_loader", lambda: loader)
    monkeypatch.setattr(auth_commands, "_revoke_remote", lambda loader, oauth: True)
    return getattr(auth_commands, handler)(types.SimpleNamespace(output="json", **kw)), json


def test_status_lists_every_team_and_keeps_its_default_fields(creds, monkeypatch, capsys):
    write, _ = creds
    loader = write(
        {"url": _CORTEX, "oauth": _set(_AT_A, "org-a", org_name="A"), "orgs": {"org-b": _set(_AT_B, "org-b")}}
    )
    rc, json = _run("handle_auth_status_command", monkeypatch, loader)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["oauth_token"] == _VALID  # the default, as before
    assert [(o["org_id"], o["default"], o["oauth_token"]) for o in out["orgs"]] == [
        ("org-a", True, "valid"),
        ("org-b", False, "valid"),
    ]


def test_logout_of_one_team_leaves_the_others(creds, monkeypatch, capsys):
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    rc, _ = _run("handle_auth_logout_command", monkeypatch, loader, org="org-b")
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert rc == 0 and stored["oauth"]["access_token"] == _AT_A and not stored.get("orgs")


def test_logout_without_an_org_drops_the_default_only(creds, monkeypatch, capsys):
    """The default's sign-in goes; the other team stays signed in, now as the default (1.17.2 sweep)."""
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    _run("handle_auth_logout_command", monkeypatch, loader)
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["oauth"]["access_token"] == _AT_B and _AT_A not in str(stored)


def test_the_sign_in_is_verified_with_the_set_it_stored(creds, monkeypatch):
    """A second team's sign-in is stored beside the default: verifying the default would report the wrong team."""
    import types

    from empirica.cli.command_handlers import auth_commands
    from empirica.core import auth

    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    monkeypatch.setattr(auth_commands, "_loader", lambda: loader)
    monkeypatch.setattr(auth, "login", lambda **kw: {"ok": True, "client_id": "c" * 20, "expires_at": None,
                        "has_refresh_token": True, "cortex_url": "https://c", "url_source": "config", "org_id": "org-b"})  # fmt: skip
    used = {}

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"org_id": "org-b"}'

    def urlopen(req, timeout=10):
        used["auth"] = req.headers.get("Authorization")
        return Resp()

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    monkeypatch.setattr("empirica.config.credentials_loader.get_credentials_loader", lambda: loader)
    auth_commands.handle_auth_login_command(types.SimpleNamespace(output="json", timeout=5, url=None))
    assert used["auth"] == f"Bearer {_AT_B}"


# --- the mesh paths prefer the practice's own team ---


def _practice(tmp_path, org):
    root = tmp_path / "practice"
    (root / ".empirica").mkdir(parents=True)
    (root / ".empirica" / "project.yaml").write_text(f"ai_id: p\norg_id: {org}\n")
    (root / "sub").mkdir()
    return root


def _two_teams(write):
    return write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})


def test_a_practice_in_another_team_uses_that_teams_sign_in(creds, tmp_path, monkeypatch):
    from empirica.core import mesh_endpoint as me

    write, _ = creds
    loader = _two_teams(write)
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    monkeypatch.chdir(_practice(tmp_path, "org-b") / "sub")  # anywhere inside the practice
    assert me.mesh_bearer(loader)["bearer"] == _AT_B


def test_a_practice_whose_team_has_no_sign_in_keeps_the_default_as_before(creds, tmp_path, monkeypatch):
    from empirica.core import mesh_endpoint as me

    write, _ = creds
    loader = _two_teams(write)
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    monkeypatch.chdir(_practice(tmp_path, "org-c"))
    assert me.mesh_bearer(loader)["bearer"] == _AT_A


def test_outside_any_practice_the_default_is_used(creds, tmp_path, monkeypatch):
    from empirica.core import mesh_endpoint as me

    write, _ = creds
    loader = _two_teams(write)
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    monkeypatch.chdir(tmp_path)
    assert me.mesh_bearer(loader)["bearer"] == _AT_A


def test_a_listener_finds_its_practices_team_by_instance(creds, tmp_path, monkeypatch):
    from empirica.core import mesh_endpoint as me

    write, _ = creds
    loader = _two_teams(write)
    root = _practice(tmp_path, "org-b")
    calls = []

    def load_registry(path=None, *, auto_dedupe=True):
        calls.append(auto_dedupe)
        return {"projects": [{"ai_id": "p", "path": str(root)}]}

    monkeypatch.setattr("empirica.api.registry.load_registry", load_registry)
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    monkeypatch.chdir(tmp_path)
    assert me.mesh_bearer(loader, instance="p")["bearer"] == _AT_B
    assert calls == [False]  # read-only: no dedupe rewrite from a credential lookup


def test_the_daemon_renews_every_family_it_owns_and_only_those(creds, monkeypatch):
    from empirica.api import serve_app

    write, path = creds
    old = time.time() - 60
    loader = write({"url": _CORTEX,
                    "oauth": _set(_AT_A, "org-a", refresh_owner="daemon", expires_at=old),
                    "orgs": {"org-b": _set(_AT_B, "org-b", refresh_owner="daemon", expires_at=old),
                             "org-c": _set(_AT_C, "org-c", refresh_owner="cli", expires_at=old)}})  # fmt: skip
    renewed = []

    def fake_default_refresh(loader, *, http=None, org_id=None):
        def _refresh(rt, ep):
            renewed.append(org_id)
            if org_id == "org-b":
                raise RuntimeError("org-b refresh refused")  # one team failing must not stop the others
            return {"access_token": f"NEW_{org_id}", "expires_at": time.time() + 3600}

        return _refresh

    monkeypatch.setattr("empirica.core.auth.default_refresh", fake_default_refresh)
    serve_app._refresh_daemon_families(loader)
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert sorted(renewed, key=str) == [None, "org-b"]  # the default by its slot; not org-c: its shell owns it
    assert stored["oauth"]["access_token"] == _NEW_A
    assert stored["orgs"]["org-c"]["access_token"] == _AT_C


def test_a_direct_caller_inside_a_practice_gets_that_teams_sign_in(creds, tmp_path, monkeypatch):
    """POSTFLIGHT sync, the session hooks, mesh connect, forgejo: they call cortex_bearer() from the practice."""
    from empirica.core.auth import cortex_oauth as co

    write, _ = creds
    loader = _two_teams(write)
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    monkeypatch.chdir(_practice(tmp_path, "org-b"))
    assert co.cortex_bearer(loader)["bearer"] == _AT_B
    monkeypatch.chdir(_practice(tmp_path / "x", "org-c"))  # no sign-in for its team: the default, as before
    assert co.cortex_bearer(loader)["bearer"] == _AT_A


# --- review of the branch (1.17.2 work): six findings ---


def test_signing_in_to_a_team_already_beside_the_default_updates_that_block(creds, monkeypatch):
    write, path = creds
    loader = write({"url": _CORTEX, "orgs": {"org-b": _set(_AT_B, "org-b")}})  # default logged out
    _login(loader, monkeypatch, new_org="org-b", new_token=_NEW_B)
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert "oauth" not in stored and stored["orgs"]["org-b"]["access_token"] == _NEW_B


def test_a_sign_in_whose_team_is_unknown_does_not_keep_the_old_teams_label(creds, monkeypatch):
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a", org_name="A")})
    monkeypatch.setattr("empirica.core.auth.cortex_oauth._whoami", lambda base, token, http=None: {})
    _login(loader, monkeypatch, new_org="org-b", new_token=_AT_B)
    stored = yaml.safe_load(path.read_text())["cortex"]["oauth"]
    assert stored["access_token"] == _AT_B and not stored.get("org_id") and not stored.get("org_name")


def test_logout_and_token_for_a_team_never_touch_an_unlabelled_default(creds, monkeypatch, capsys):
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A)})  # team not recorded
    rc, _ = _run("handle_auth_logout_command", monkeypatch, loader, org="org-b")
    assert rc == 0 and yaml.safe_load(path.read_text())["cortex"]["oauth"]["access_token"] == _AT_A
    capsys.readouterr()
    _run("handle_auth_token_command", monkeypatch, loader, org="org-b", headers=False)
    assert _AT_A not in capsys.readouterr().out


def test_a_single_sign_in_takes_the_old_path_inside_any_practice(creds, tmp_path, monkeypatch):
    """One team on the machine (every seat before this change): no org is threaded at all, so nothing can differ."""
    from empirica.core.auth import cortex_oauth as co

    write, _ = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A)})
    seen = []
    monkeypatch.setattr(co, "custody_refresh", lambda loader, http=None, **kw: (seen.append(kw), (None, "cli"))[1])
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    monkeypatch.chdir(_practice(tmp_path, "org-a"))
    assert co.cortex_bearer(loader)["bearer"] == _AT_A and seen == [{}]


def test_a_live_default_whose_team_cannot_be_read_is_kept(creds, monkeypatch):
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A)})  # valid token, team not recorded, /me fails for it
    _login(loader, monkeypatch, new_org="org-b", new_token=_AT_B)
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["oauth"]["access_token"] == _AT_A and stored["orgs"]["org-b"]["access_token"] == _AT_B


def test_a_listener_never_borrows_its_working_directorys_team(creds, tmp_path, monkeypatch):
    from empirica.core import mesh_endpoint as me

    write, _ = creds
    loader = _two_teams(write)
    monkeypatch.setattr("empirica.api.registry.load_registry", lambda path=None, *, auto_dedupe=True: {"projects": []})
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    monkeypatch.chdir(_practice(tmp_path, "org-b"))  # started inside another team's practice
    assert me.mesh_bearer(loader, instance="unknown-practice")["bearer"] == _AT_A


# --- several teams need Empirica Cortex (David, 2026-11-16): not trivial to get from a server copying the routes ---


@pytest.mark.parametrize(
    "url",
    ["https://cortex.example.com", "http://cortex.getempirica.com", "https://getempirica.com.evil.test",
     "https://evilgetempirica.com", "http://localhost:8000"],
)  # fmt: skip
def test_off_cortex_a_hand_written_second_team_is_not_read(creds, monkeypatch, url):
    write, _ = creds
    loader = write({"url": url, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    assert loader.get_cortex_oauth(org_id="org-b") == {}
    assert [slot for slot, _ in loader.cortex_oauth_sets()] == [None]


@pytest.mark.parametrize(
    "url", ["https://cortex.getempirica.com", "https://getempirica.com", "https://eu.cortex.getempirica.com"]
)
def test_on_empirica_cortex_every_team_is_read(creds, url):
    write, _ = creds
    loader = write({"url": url, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    assert loader.get_cortex_oauth(org_id="org-b")["access_token"] == _AT_B


def test_off_cortex_a_second_team_replaces_the_default_as_before(creds, monkeypatch):
    write, path = creds
    loader = write({"url": "https://cortex.example.com", "oauth": _set(_AT_A, "org-a")})
    result = _login(loader, monkeypatch, new_org="org-b", new_token=_AT_B, org="org-b")
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["oauth"]["access_token"] == _AT_B and "orgs" not in stored
    assert result["slot"] is None and "Empirica Cortex" in result["note"]


def test_off_cortex_a_write_to_another_teams_slot_is_refused(creds):
    write, _ = creds
    loader = write({"url": "https://cortex.example.com", "oauth": _set(_AT_A, "org-a")})
    with pytest.raises(ValueError, match="Empirica Cortex"):
        loader.save_cortex_oauth(slot="org-b", access_token=_AT_B)


# --- heartbeats beat with their practice's team (goal 1783ba50) ---


def _registry_with(monkeypatch, tmp_path, ai_id, org):
    root = _practice(tmp_path / ai_id, org)
    monkeypatch.setattr(
        "empirica.api.registry.load_registry",
        lambda path=None, *, auto_dedupe=True: {"projects": [{"ai_id": ai_id, "path": str(root)}]},
    )


def test_the_listener_heartbeat_uses_its_practices_team(creds, tmp_path, monkeypatch):
    from empirica.core.loop_scheduler import heartbeat as hb

    write, _ = creds
    _two_teams(write)
    _registry_with(monkeypatch, tmp_path, "p", "org-b")
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    monkeypatch.chdir(tmp_path)
    sent = []
    emitter = hb.HeartbeatEmitter(ai_id="p", _post_fn=lambda url, body, headers, timeout: sent.append(headers) or 200)
    emitter.emit_once()
    assert sent and sent[0]["Authorization"] == f"Bearer {_AT_B}"


def test_a_practitioner_beats_with_its_own_practices_team(creds, tmp_path, monkeypatch):
    from empirica.core.loop_scheduler import practitioner_heartbeat as ph

    write, _ = creds
    _two_teams(write)
    _registry_with(monkeypatch, tmp_path, "p", "org-b")
    monkeypatch.setattr("empirica.core.auth.credential_health.dead_reason", lambda cred: None)
    monkeypatch.chdir(tmp_path)
    sent = []
    emitter = ph.PractitionerHeartbeatEmitter(
        _list_fn=lambda: [{"claude_session_id": "s1", "practice_ai_id": "p"}],
        _refresh_fn=lambda: None,
        _post_fn=lambda url, body, headers, timeout: sent.append(headers) or 200,
        _get_fn=lambda url, key, timeout: {"projects": []},
    )
    emitter.emit_once()
    assert sent and sent[0]["Authorization"] == f"Bearer {_AT_B}"


# --- 1.17.2 pre-publish sweep ---


def test_a_sign_in_to_another_server_never_sends_it_the_stored_token_and_stores_nothing_beside(creds, monkeypatch):
    """login --url <other> decided the slot from the stored URL and probed the default's token against the other server."""
    from empirica.core.auth import cortex_oauth as co

    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A)})  # default with no recorded team: would be probed
    probed = []
    real_whoami = co._whoami
    monkeypatch.setattr(
        co,
        "_whoami",
        lambda base, token, http=None: (probed.append((base, token)), real_whoami(base, token, http=http))[1],
    )
    monkeypatch.setattr(co, "resolve_cortex_url", lambda loader, url=None: ("https://copy.test", "flag"))
    _login(loader, monkeypatch, new_org="org-b", new_token=_AT_B)
    assert all(token != _AT_A for _, token in probed), probed
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert "orgs" not in stored and stored["oauth"]["access_token"] == _AT_B


def test_logging_out_of_the_default_promotes_a_remaining_team(creds, monkeypatch, capsys):
    write, path = creds
    loader = write({"url": _CORTEX, "oauth": _set(_AT_A, "org-a"), "orgs": {"org-b": _set(_AT_B, "org-b")}})
    _run("handle_auth_logout_command", monkeypatch, loader)
    stored = yaml.safe_load(path.read_text())["cortex"]
    assert stored["oauth"]["access_token"] == _AT_B and stored["oauth"]["org_id"] == "org-b" and not stored.get("orgs")

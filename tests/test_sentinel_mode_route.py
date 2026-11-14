"""GET/PATCH /api/v1/sentinel/mode: the local daemon's door to `empirica sentinel mode` (prop_ycoxhq4nmzdrlhgjinyjei4sse).

The extension talks only to the daemon, and the setting must be user-settable there with the value in force and its source
shown. The route and the CLI build their answer with one function (sentinel_discipline.mode_report), so they cannot drift.
"""

from __future__ import annotations

import pytest

from empirica.api.routes import sentinel as route
from empirica.core import sentinel_discipline as sd


@pytest.fixture()
def env(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(sd.ENV_MODE, raising=False)
    monkeypatch.delenv(sd.ENV_BUDGET, raising=False)
    practice = tmp_path / "practice-A"
    (practice / ".empirica").mkdir(parents=True)
    (practice / ".empirica" / "project.yaml").write_text("ai_id: practice-a\n")
    monkeypatch.setattr(route, "_resolve_registered", lambda key: practice if key == "practice-a" else None)
    app = FastAPI()
    app.include_router(route.router)
    return TestClient(app), practice, home


def test_get_by_path_shows_the_built_in_values_and_their_source(env):
    client, practice, _ = env
    body = client.get("/api/v1/sentinel/mode", params={"project": str(practice)}).json()
    assert body["ok"] is True
    assert (body["mode"], body["mode_source"]) == (sd.DEFAULT_MODE, "built-in")
    assert (body["max_act_without_gate"], body["max_act_without_gate_source"]) == (sd.DEFAULT_BUDGET, "built-in")
    # The ledger is per terminal instance; the daemon has none, so it says it cannot count rather than reporting 0.
    assert body["out_of_loop_acts_pending"] is None


def test_get_by_ai_id_resolves_through_the_registry(env):
    client, practice, _ = env
    client.patch("/api/v1/sentinel/mode", json={"mode": "strict", "scope": "practice", "project": str(practice)})
    body = client.get("/api/v1/sentinel/mode", params={"project": "practice-a"}).json()
    assert body["mode"] == "strict" and str(practice) in body["mode_source"]


def test_an_unknown_project_is_404_not_the_seat_values(env):
    client, _, _ = env
    assert client.get("/api/v1/sentinel/mode", params={"project": "nobody"}).status_code == 404


def test_patch_practice_writes_the_practice_file_and_answers_with_what_is_in_force(env):
    client, practice, _ = env
    resp = client.patch(
        "/api/v1/sentinel/mode",
        json={"mode": "strict", "max_act_without_gate": 5, "scope": "practice", "project": str(practice)},
    )
    assert resp.status_code == 200
    body = resp.json()
    target = practice / ".empirica" / "config.yaml"
    assert body["written"] == str(target)
    assert (body["mode"], body["max_act_without_gate"]) == ("strict", 5)
    assert str(target) in body["mode_source"]
    assert body["not_in_force"] == []
    assert sd.effective(practice)["budget"] == 5  # what the gate reads


def test_patch_seat_writes_the_home_file(env):
    client, practice, home = env
    body = client.patch("/api/v1/sentinel/mode", json={"max_act_without_gate": 9, "scope": "seat"}).json()
    assert body["written"] == str(home / ".empirica" / "config.yaml")
    assert sd.effective(practice)["budget"] == 9


def test_bad_values_are_refused_with_the_cli_message(env):
    client, practice, _ = env
    bad_mode = client.patch(
        "/api/v1/sentinel/mode", json={"mode": "loose", "scope": "practice", "project": str(practice)}
    )
    assert bad_mode.status_code == 400 and "strict, discipline, off" in bad_mode.json()["detail"]
    too_big = client.patch("/api/v1/sentinel/mode", json={"max_act_without_gate": 1001, "scope": "seat"})
    assert too_big.status_code == 400 and "0 to 1000" in too_big.json()["detail"]
    assert not (practice / ".empirica" / "config.yaml").exists()


def test_a_practice_write_needs_a_practice_and_a_write_needs_a_value(env):
    client, _, _ = env
    assert client.patch("/api/v1/sentinel/mode", json={"mode": "strict", "scope": "practice"}).status_code == 400
    assert client.patch("/api/v1/sentinel/mode", json={"scope": "seat"}).status_code == 400


def test_a_value_an_environment_variable_overrides_is_reported_as_not_in_force(env, monkeypatch):
    client, practice, _ = env
    monkeypatch.setenv(sd.ENV_BUDGET, "7")
    body = client.patch(
        "/api/v1/sentinel/mode", json={"max_act_without_gate": 5, "scope": "practice", "project": str(practice)}
    ).json()
    assert body["max_act_without_gate"] == 7
    assert body["not_in_force"] == [f"max_act_without_gate: written but not in force, env {sd.ENV_BUDGET} overrides"]


def test_the_cli_builds_its_answer_with_the_same_function():
    import inspect

    from empirica.cli.command_handlers import cockpit_commands

    assert "sd.mode_report(" in inspect.getsource(cockpit_commands.handle_sentinel_mode_command)


def test_the_daemon_serves_both_methods_and_its_cors_lets_the_extension_send_the_write():
    # The daemon's CORS lists GET, POST, PATCH and DELETE; a PUT here would have failed the extension's preflight.
    from fastapi.testclient import TestClient

    from empirica.api.serve_app import create_serve_app

    app = create_serve_app()
    assert sorted(app.openapi()["paths"]["/api/v1/sentinel/mode"]) == ["get", "patch"]
    preflight = TestClient(app).options(
        "/api/v1/sentinel/mode",
        headers={"Origin": "chrome-extension://abc", "Access-Control-Request-Method": "PATCH"},
    )
    assert preflight.status_code == 200


def test_a_relative_name_is_looked_up_not_read_against_the_daemon_s_directory(env, monkeypatch, tmp_path):
    client, practice, _ = env
    monkeypatch.chdir(practice.parent)  # "practice-A" now exists relative to the cwd
    assert client.get("/api/v1/sentinel/mode", params={"project": "practice-A"}).status_code == 404
    assert (
        client.get("/api/v1/sentinel/mode", params={"project": "practice-a"}).status_code == 200
    )  # the registered ai_id

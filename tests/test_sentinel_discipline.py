"""The Sentinel's discipline mode (goal 543fd1c6): settings with their source, what counts as a trivial act, and the budget.

strict stays the built-in default until the pilot, so nothing here changes the gate for anyone who has not opted in. In
discipline mode only project-local changes whose effect is readable from the call run outside a transaction, up to the budget;
outward or irreversible acts always need one. Each check below sits beside its near miss.
"""

from __future__ import annotations

import pytest

from empirica.core import sentinel_discipline as sd


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    (h / ".empirica").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv(sd.ENV_MODE, raising=False)
    monkeypatch.delenv(sd.ENV_BUDGET, raising=False)
    from empirica.config import path_resolver

    monkeypatch.setattr(path_resolver, "_git_root_cache", None)  # per process in the CLI; per test here
    proj = tmp_path / "proj"
    (proj / ".empirica").mkdir(parents=True)
    (proj / ".empirica" / "project.yaml").write_text("project_id: p\nname: proj\nai_id: proj\n")
    return h, proj


def _is_read(cmd: str) -> bool:
    parts = cmd.split()
    return len(parts) > 1 and parts[1].endswith(("-list", "-show"))


# ── settings ────────────────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_built_in_values_apply_when_nothing_is_set(home):
    eff = sd.effective(home[1])
    assert (eff["mode"], eff["mode_source"]) == ("strict", "built-in")
    assert (eff["budget"], eff["budget_source"]) == (sd.DEFAULT_BUDGET, "built-in")


def test_precedence_is_env_then_practice_then_seat(home, monkeypatch):
    h, proj = home
    (h / ".empirica" / "config.yaml").write_text("sentinel:\n  mode: off\n  max_act_without_gate: 9\n")
    eff = sd.effective(proj)
    assert (eff["mode"], eff["budget"]) == ("off", 9) and eff["mode_source"].startswith("seat")

    (proj / ".empirica" / "config.yaml").write_text("version: '2.0'\nsentinel:\n  mode: discipline\n")
    eff = sd.effective(proj)
    assert (eff["mode"], eff["budget"]) == ("discipline", 9)  # each setting resolves on its own
    assert eff["mode_source"].startswith("practice") and eff["budget_source"].startswith("seat")

    monkeypatch.setenv(sd.ENV_MODE, "strict")
    monkeypatch.setenv(sd.ENV_BUDGET, "0")
    eff = sd.effective(proj)
    assert (eff["mode"], eff["budget"]) == ("strict", 0) and eff["mode_source"] == f"env {sd.ENV_MODE}"


@pytest.mark.parametrize("value", ["lenient", "", "DISCIPLINE "])
def test_an_unknown_mode_is_ignored_and_reported(home, monkeypatch, value):
    monkeypatch.setenv(sd.ENV_MODE, value)
    eff = sd.effective(home[1])
    if value.strip().lower() == "discipline":
        assert eff["mode"] == "discipline"  # case and spaces are not a different mode
    else:
        assert eff["mode"] == "strict" and eff["ignored"]


@pytest.mark.parametrize("value", ["-1", "three", "1001", "true", "2.5"])
def test_a_budget_that_is_not_a_whole_number_in_range_is_ignored(home, monkeypatch, value):
    monkeypatch.setenv(sd.ENV_BUDGET, value)
    eff = sd.effective(home[1])
    assert eff["budget"] == sd.DEFAULT_BUDGET and eff["ignored"]


def test_writing_a_setting_keeps_every_other_line(home):
    path = home[0] / ".empirica" / "config.yaml"
    path.write_text("# Empirica Configuration\n# keep me\nembeddings:\n  provider: ollama\n")

    sd.write_setting(path, mode="discipline")
    sd.write_setting(path, budget=2)  # a second write keeps the first

    text = path.read_text()
    assert text.startswith("# Empirica Configuration\n# keep me\nembeddings:\n  provider: ollama\n")
    assert sd.effective(None)["mode"] == "discipline" and sd.effective(None)["budget"] == 2
    assert text.count("sentinel:") == 1


def test_a_sentinel_block_in_the_middle_is_replaced_in_place(home):
    path = home[0] / ".empirica" / "config.yaml"
    path.write_text("a: 1\nsentinel:\n  mode: strict\n  max_act_without_gate: 4\nb: 2\n")

    sd.write_setting(path, mode="off")

    assert path.read_text() == 'a: 1\nsentinel:\n  mode: "off"\n  max_act_without_gate: 4\nb: 2\n'
    assert sd.effective(None)["mode"] == "off"  # quoted, because YAML reads a bare off as false


@pytest.mark.parametrize("kwargs", [{"mode": "loose"}, {"budget": -1}, {"budget": 5000}])
def test_an_invalid_setting_is_refused_and_nothing_is_written(home, kwargs):
    path = home[0] / ".empirica" / "config.yaml"
    with pytest.raises(ValueError):
        sd.write_setting(path, **kwargs)
    assert not path.exists()


# ── what is trivial ─────────────────────────────────────────────────────────────────────────────────────────────────────


def _judge(home, tool, tool_input):
    proj = home[1]
    return sd.judge(tool, tool_input, cwd=str(proj), project_root=str(proj), empirica_is_read=_is_read)


@pytest.mark.parametrize(
    "tool, tool_input",
    [
        ("Edit", {"file_path": "src/app.py", "old_string": "a", "new_string": "b"}),
        ("Write", {"file_path": "README.md", "content": "x"}),
        ("Bash", {"command": "sed -i 's/a/b/' src/app.py"}),
        ("Bash", {"command": "git add -A && git commit -m 'fix typo'"}),
        ("Bash", {"command": "echo note >> NOTES.md"}),
    ],
)
def test_a_project_local_change_readable_from_the_call_is_trivial(home, tool, tool_input):
    verdict = _judge(home, tool, tool_input)
    assert verdict["trivial"] is True, verdict


@pytest.mark.parametrize(
    "tool, tool_input, why",
    [
        ("Bash", {"command": "git commit -m x && git push"}, "vcs_remote"),
        ("Bash", {"command": "git tag v1.2.0"}, "publishes"),
        ("Bash", {"command": "npm publish"}, "publishes"),
        ("Bash", {"command": "twine upload dist/*"}, "publishes"),
        ("Bash", {"command": "gh release create v1"}, "publishes"),
        ("Bash", {"command": "pip install requests"}, "install"),
        ("Bash", {"command": "rm -rf build"}, "delete_recursive"),
        ("Bash", {"command": "ssh host 'touch x'"}, "remote_exec"),
        ("Bash", {"command": "curl -XPOST https://x/api"}, "network_send"),
        ("Bash", {"command": "echo x >> ~/.bashrc"}, "config_write"),
        ("Bash", {"command": "kill 1234"}, "process"),
        ("Bash", {"command": "python3 -c 'print(1)'"}, "cannot be read"),
        ("Bash", {"command": "echo $(date) > f"}, "cannot be read"),
        ("Edit", {"file_path": "/etc/hosts", "old_string": "a", "new_string": "b"}, "outside_write"),
        ("mcp__cortex__cortex_propose", {"title": "x"}, "not a file edit"),
    ],
)
def test_outward_or_unreadable_acts_are_never_trivial(home, tool, tool_input, why):
    verdict = _judge(home, tool, tool_input)
    assert verdict["trivial"] is False and why in verdict["why"], verdict


def test_listing_tags_is_not_publishing(home):
    assert "publishes" not in _judge(home, "Bash", {"command": "git tag -l 'v1*'"})["why"]


# ── the budget ──────────────────────────────────────────────────────────────────────────────────────────────────────────


def _decide(home, command="sed -i s/a/b/ f.txt", suffix="_t1"):
    proj = home[1]
    return sd.decide(
        "Bash", {"command": command}, cwd=str(proj), project_root=str(proj), suffix=suffix, empirica_is_read=_is_read
    )


def _answer(home, command="sed -i s/a/b/ f.txt", suffix="_t1") -> tuple[str, str]:
    result = _decide(home, command, suffix)
    assert result is not None, "discipline mode always answers"
    return result


def test_strict_has_no_opinion(home):
    assert _decide(home) is None
    assert sd.ledger("_t1") == []


def test_off_allows_everything(home, monkeypatch):
    monkeypatch.setenv(sd.ENV_MODE, "off")
    assert _decide(home, "git push")[0] == "allow"


def test_discipline_allows_trivial_acts_up_to_the_budget_then_asks_for_a_transaction(home, monkeypatch):
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    monkeypatch.setenv(sd.ENV_BUDGET, "2")

    assert _decide(home)[0] == "allow"
    assert _decide(home)[0] == "allow"
    decision, message = _answer(home)

    assert decision == "deny" and "No longer trivial" in message and "PREFLIGHT" in message
    assert len(sd.ledger("_t1")) == 2


def test_a_budget_of_zero_allows_no_act(home, monkeypatch):
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    monkeypatch.setenv(sd.ENV_BUDGET, "0")
    assert _decide(home)[0] == "deny"


def test_an_outward_act_is_refused_even_with_budget_left_and_is_not_counted(home, monkeypatch):
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    decision, message = _answer(home, "git push")
    assert decision == "deny" and "not trivial" in message
    assert sd.ledger("_t1") == []


def test_each_instance_spends_its_own_budget(home, monkeypatch):
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    monkeypatch.setenv(sd.ENV_BUDGET, "1")
    assert _decide(home, suffix="_a")[0] == "allow"
    assert _decide(home, suffix="_b")[0] == "allow"
    assert _decide(home, suffix="_a")[0] == "deny"


def test_preflight_takes_the_ledger_and_the_budget_starts_again(home, monkeypatch):
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    monkeypatch.setenv(sd.ENV_BUDGET, "1")
    _decide(home, "git commit -m x")

    taken = sd.take_ledger("_t1")

    assert [e["command_class"] for e in taken] == ["git"]
    assert "git commit -m x" not in str(taken), "a command is kept only as a hash"
    assert _decide(home)[0] == "allow"


# ── the gate consults it at its out-of-transaction refusals ──────────────────────────────────────────────────────────────


@pytest.fixture
def gate(home, monkeypatch):
    import importlib.util
    from pathlib import Path

    hooks = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks"
    spec = importlib.util.spec_from_file_location("sentinel_gate_discipline", hooks / "sentinel-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    proj = home[1]
    monkeypatch.setattr(module, "_record_input", {"session_id": "s-1", "cwd": str(proj)})
    monkeypatch.setattr(module, "_instance_suffix", lambda: "_gate_test")
    monkeypatch.chdir(proj)
    return module


def _edit(proj):
    return {"file_path": str(proj / "a.py"), "old_string": "a", "new_string": "b"}


def test_strict_keeps_the_no_preflight_refusal(gate, home):
    decision, message = gate._handle_no_preflight("Edit", _edit(home[1]), "s-1", "")
    assert decision == "deny" and "No open transaction" in message


def test_discipline_allows_a_trivial_act_before_preflight_then_stops(gate, home, monkeypatch):
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    monkeypatch.setenv(sd.ENV_BUDGET, "1")

    first = gate._handle_no_preflight("Edit", _edit(home[1]), "s-1", "")
    second = gate._handle_no_preflight("Edit", _edit(home[1]), "s-1", "")

    assert first[0] == "allow" and "act 1 of 1" in first[1]
    assert second[0] == "deny" and "No longer trivial" in second[1]
    assert gate._decision_rule == "discipline-budget"


def test_discipline_still_refuses_a_push_before_preflight(gate, home, monkeypatch):
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    decision, message = gate._handle_no_preflight("Bash", {"command": "git push origin main"}, "s-1", "")
    assert decision == "deny" and "vcs_remote" in message


def test_after_postflight_discipline_answers_too(gate, home, monkeypatch, capsys):
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    with pytest.raises(SystemExit):
        gate._handle_closed_transaction("Edit", _edit(home[1]))
    assert '"permissionDecision": "allow"' in capsys.readouterr().out

    monkeypatch.setenv(sd.ENV_MODE, "strict")
    with pytest.raises(SystemExit):
        gate._handle_closed_transaction("Edit", _edit(home[1]))
    out = capsys.readouterr().out
    assert '"permissionDecision": "deny"' in out and "Epistemic loop closed" in out


def test_without_a_resolvable_practice_every_write_is_outside_and_refused(gate, home, monkeypatch):
    """Fail safe: when the call's practice cannot be found, a write is not project-local, so it needs a transaction."""
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    (home[1] / ".empirica" / "project.yaml").unlink()
    decision, message = gate._handle_no_preflight("Edit", _edit(home[1]), "s-1", "")
    assert decision == "deny" and "outside_write" in message


# ── empirica sentinel mode ──────────────────────────────────────────────────────────────────────────────────────────────


def _cli(*argv):
    import json

    from empirica.cli.cli_core import create_argument_parser
    from empirica.cli.command_handlers.cockpit_commands import handle_sentinel_group_command

    args = create_argument_parser().parse_args(["sentinel", "mode", *argv, "--output", "json"])
    return args, handle_sentinel_group_command, json


def _run_cli(capsys, *argv):
    args, handler, json = _cli(*argv)
    rc = handler(args)
    return rc, json.loads(capsys.readouterr().out)


@pytest.fixture
def in_practice(home, monkeypatch):
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=home[1], check=True)
    monkeypatch.chdir(home[1])
    return home


def test_the_command_shows_what_is_in_force_and_where_it_came_from(in_practice, capsys):
    rc, out = _run_cli(capsys)
    assert rc == 0
    assert (out["mode"], out["mode_source"]) == ("strict", "built-in")
    assert out["max_act_without_gate"] == sd.DEFAULT_BUDGET and out["written"] is None


def test_setting_it_writes_the_practice_config(in_practice, capsys):
    rc, out = _run_cli(capsys, "discipline", "--max-act-without-gate", "2")
    assert rc == 0 and out["written"].endswith(".empirica/config.yaml")
    assert out["mode"] == "discipline" and out["mode_source"].startswith("practice")
    assert out["max_act_without_gate"] == 2


def test_seat_writes_the_seat_config(in_practice, capsys):
    rc, out = _run_cli(capsys, "off", "--seat")
    assert rc == 0 and out["mode_source"].startswith("seat")
    assert (in_practice[0] / ".empirica" / "config.yaml").exists()


def test_outside_a_practice_it_asks_for_seat(home, tmp_path, monkeypatch, capsys):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    rc, out = _run_cli(capsys, "discipline")
    assert rc == 1 and "--seat" in out["error"]


def test_the_parser_names_the_flag_as_the_design_does():
    from empirica.cli.cli_core import create_argument_parser

    ns = create_argument_parser().parse_args(["sentinel", "mode", "--max-act-without-gate", "4"])
    assert ns.max_act_without_gate == 4


# ── PREFLIGHT takes the ledger ──────────────────────────────────────────────────────────────────────────────────────────


def test_preflight_takes_the_ledger_into_the_transaction_file(home, monkeypatch):
    import json

    from empirica.cli.command_handlers import _workflow_preflight as wp

    monkeypatch.setattr(wp.R, "instance_suffix", staticmethod(lambda: "_pf"))
    monkeypatch.setenv(sd.ENV_MODE, "discipline")
    proj = home[1]
    sd.decide(
        "Edit",
        {"file_path": str(proj / "a.py"), "old_string": "a", "new_string": "b"},
        cwd=str(proj),
        project_root=str(proj),
        suffix="_pf",
        empirica_is_read=_is_read,
    )
    tx = proj / ".empirica" / "active_transaction_pf.json"
    tx.write_text(json.dumps({"transaction_id": "t"}))

    taken = wp._preflight_take_out_of_loop(str(proj))

    assert [e["paths"] for e in taken] == [[str(proj / "a.py")]]
    assert json.loads(tx.read_text())["prior_untracked"] == taken
    assert sd.ledger("_pf") == []  # the budget starts again


def test_preflight_with_an_empty_ledger_adds_nothing(home, monkeypatch):
    from empirica.cli.command_handlers import _workflow_preflight as wp

    monkeypatch.setattr(wp.R, "instance_suffix", staticmethod(lambda: "_pf2"))
    assert wp._preflight_take_out_of_loop(str(home[1])) == []


def test_doctor_names_the_mode_and_warns_on_an_ignored_setting(home, monkeypatch):
    from empirica.cli.command_handlers.doctor import check_sentinel_mode

    ok = check_sentinel_mode(home[1])
    assert ok.status == "PASS" and "strict (built-in)" in ok.detail

    (home[1] / ".empirica" / "config.yaml").write_text("sentinel:\n  max_act_without_gate: lots\n")
    bad = check_sentinel_mode(home[1])
    assert bad.status == "WARN" and "lots" in bad.hint

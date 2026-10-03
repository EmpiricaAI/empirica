"""`diagnose --frontend ecodex`: the instance-isolation check tests what the contract is, not where one string sits.

ecodex moved the TUI's key out of chatwidget.rs on purpose: one TUI process can host several threads, so a
process-global set_var would leak between them; the key is now set on the spawned statusline command's own
environment (plugin_statusline_runtime.rs). The check grepped chatwidget.rs for the literal and its hint told the
TUI to call `unsafe set_var`, so it FAILed on a correct install.

Now: the TUI source names the key anywhere under tui/src (as a per-command env or as set_var), AND empirica's own
half of the contract is exercised, not read: run the statusline with EMPIRICA_INSTANCE_ID set to a thread id whose
instance file exists and see that it resolves that practice, and that it does not without the variable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.cli.command_handlers import diagnose_ecodex as de

REPO = Path(__file__).parent.parent
REAL_STATUSLINE = REPO / "empirica" / "plugins" / "claude-code-integration" / "scripts" / "statusline_empirica.py"


def _fake_ecodex(
    tmp_path: Path, *, tui_files: dict[str, str], plugin_text: str = 'cmd.env("EMPIRICA_INSTANCE_ID", id);'
):
    root = tmp_path / "ecodex"
    (root / "codex-rs" / "codex-empirica-plugin" / "src").mkdir(parents=True)
    (root / "codex-rs" / "Cargo.toml").write_text("[workspace]\n")
    (root / "codex-rs" / "codex-empirica-plugin" / "src" / "empirica_cli.rs").write_text(plugin_text)
    tui = root / "codex-rs" / "tui" / "src"
    tui.mkdir(parents=True)
    for name, text in tui_files.items():
        (tui / name).write_text(text)
    return root


@pytest.fixture
def check(monkeypatch):
    """The check with the behavioural probe stubbed out, so the source half is tested on its own."""

    def run(root: Path):
        monkeypatch.setenv("ECODEX_REPO_ROOT", str(root))
        monkeypatch.setattr(de, "_statusline_resolves_by_instance_id", lambda _script=None: (True, "stub"))
        return de.check_ecodex_instance_isolation_key()

    return run


# ── the source half ─────────────────────────────────────────────────────────


def test_a_per_command_env_in_the_statusline_runtime_passes_even_though_chatwidget_names_nothing(tmp_path, check):
    """The exact shape ecodex shipped: the key lives in plugin_statusline_runtime.rs, not chatwidget.rs."""
    root = _fake_ecodex(
        tmp_path,
        tui_files={
            "chatwidget.rs": "// no key here\n",
            "plugin_statusline_runtime.rs": 'cmd.env("EMPIRICA_INSTANCE_ID", thread_id);\n',
        },
    )

    assert check(root).status == de.PASS


def test_the_older_set_var_form_in_chatwidget_still_passes(tmp_path, check):
    root = _fake_ecodex(
        tmp_path, tui_files={"chatwidget.rs": 'unsafe { std::env::set_var("EMPIRICA_INSTANCE_ID", id) }\n'}
    )

    assert check(root).status == de.PASS


def test_a_tui_that_names_the_key_nowhere_fails_and_says_where(tmp_path, check):
    root = _fake_ecodex(tmp_path, tui_files={"chatwidget.rs": "// nothing\n", "other.rs": "fn main() {}\n"})

    result = check(root)

    assert result.status == de.FAIL and "tui" in result.detail


def test_the_hint_no_longer_demands_a_process_global_set_var(tmp_path, check):
    root = _fake_ecodex(tmp_path, tui_files={"chatwidget.rs": "// nothing\n"})

    hint = check(root).hint or ""

    assert "set_var" not in hint or "not" in hint.lower() or "per-command" in hint.lower()


def test_a_plugin_that_never_names_the_key_still_fails(tmp_path, check):
    root = _fake_ecodex(
        tmp_path,
        tui_files={"plugin_statusline_runtime.rs": 'cmd.env("EMPIRICA_INSTANCE_ID", id);'},
        plugin_text="fn x() {}",
    )

    result = check(root)

    assert result.status == de.FAIL and "empirica_cli.rs" in result.detail


# ── the behavioural half ────────────────────────────────────────────────────


def test_the_real_statusline_resolves_a_practice_from_the_instance_id_and_not_without_it():
    """POSITIVE CONTROL for the probe: empirica's own script honours the contract, and the variable is what does it."""
    ok, detail = de._statusline_resolves_by_instance_id(REAL_STATUSLINE)

    assert ok is True, detail


def test_a_script_that_ignores_the_instance_id_fails_the_probe(tmp_path):
    stub = tmp_path / "statusline_stub.py"
    stub.write_text('print("[no project]")\n')

    ok, detail = de._statusline_resolves_by_instance_id(stub)

    assert ok is False and "did not" in detail


def test_a_script_that_resolves_a_practice_regardless_of_the_variable_proves_nothing(tmp_path):
    """If it would print the practice without EMPIRICA_INSTANCE_ID too, the variable was not what resolved it."""
    stub = tmp_path / "statusline_always.py"
    stub.write_text('import sys\nprint("[probe-practice] always")\n')

    ok, detail = de._statusline_resolves_by_instance_id(stub)

    assert ok is False and "without" in detail


def test_the_check_fails_when_the_probe_fails(tmp_path, monkeypatch):
    root = _fake_ecodex(tmp_path, tui_files={"plugin_statusline_runtime.rs": 'cmd.env("EMPIRICA_INSTANCE_ID", id);'})
    monkeypatch.setenv("ECODEX_REPO_ROOT", str(root))
    monkeypatch.setattr(de, "_statusline_resolves_by_instance_id", lambda _script=None: (False, "did not resolve"))

    result = de.check_ecodex_instance_isolation_key()

    assert result.status == de.FAIL and "did not resolve" in result.detail

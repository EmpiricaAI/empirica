"""What a shell command will change, inferred from its text, on the PreToolUse decision line.

ecodex's ruling (prop_4avo25wpczfcpmv7ug5fbztztq): classify from the text now, as features and an audit trail, never as a
training or evaluation label. The calls whose text cannot settle it (inline interpreter code, command substitution, a heredoc)
are marked unreliable; a sampled before-and-after comparison measures how often text and effect agree. empirica's own verbs
are read by verb, since many only read: the gate's is_read_shaped_empirica_verb decides.
"""

from __future__ import annotations

import pytest

from empirica.core import decision_record as dr


def _is_read(cmd: str) -> bool:
    parts = cmd.split()
    return len(parts) > 1 and parts[0] == "empirica" and parts[1].endswith(("-list", "-show", "-search", "-status"))


def infer(command, cwd="/work/proj", project="/work/proj"):
    return dr.infer_shell_effects(command, cwd=cwd, project_path=project, empirica_is_read=_is_read)


@pytest.mark.parametrize(
    "command, classes",
    [
        ("git status --short", []),
        ("git log --oneline -5 | cat", []),
        ("git commit -m 'x'", ["vcs_local"]),
        ("git add -A && git commit -q -m x && git push -q origin develop", ["vcs_local", "vcs_remote"]),
        ("gh pr create --title x --body y", ["vcs_remote"]),
        ("gh run list --limit 1", []),
        ("pip install -e . --no-deps", ["install"]),
        ("uv pip install foo", ["install"]),
        ("npm install", ["install"]),
        ("rm -rf build", ["delete_recursive", "project_write"]),
        ("rm notes.txt", ["project_write"]),
        ("ssh root@host uptime", ["remote_exec"]),
        ("scp a.txt host:/tmp/", ["network_send"]),
        ("curl -s https://example.com", []),
        ("curl -X POST -d '{}' https://example.com/api", ["network_send"]),
        ("systemctl --user restart empirica-listener", ["process"]),
        ("systemctl --user status empirica-listener", []),
        ("kill 1234", ["process"]),
        ("empirica goals-list", []),
        ("empirica finding-log --finding x", ["empirica_state"]),
        ("cat a | grep b | wc -l", []),
        ("echo hi > out.txt", ["project_write"]),
        ("echo hi >> ~/.bashrc", ["config_write"]),
        ("sed -i 's/a/b/' src/app.py", ["project_write"]),
        ("sed -n 1,5p src/app.py", []),
        ("find . -name '*.pyc' -delete", ["delete_recursive"]),
        ("mkdir -p /tmp/x", ["outside_write"]),
        ("cd /work/proj && ls", []),
        ("FOO=1 sudo frobnicate --all", ["unknown"]),
    ],
)
def test_a_command_is_classed_by_what_its_text_says_it_does(command, classes):
    assert infer(command)["classes"] == classes


@pytest.mark.parametrize(
    "command",
    [
        "python3 -c 'import os; os.remove(\"x\")'",
        "bash -c 'rm -rf x'",
        'node -e \'require("fs").rmSync("x")\'',
        'eval "$CMD"',
        "echo $(rm -rf x)",
        "echo `date`",
        "cat <<'EOF' > f\nx\nEOF",
    ],
)
def test_text_that_cannot_settle_it_is_marked_unreliable(command):
    assert infer(command)["reliable"] is False


def test_plain_commands_are_reliable():
    assert infer("git status")["reliable"] is True
    assert infer("rm -rf build && git push")["reliable"] is True


def test_the_record_carries_the_inference_apart_from_any_observation():
    rec = dr.build(
        tool_name="Bash",
        tool_input={"command": "git push"},
        decision="allow",
        rule="pipeline",
        inferred=infer("git push"),
    )

    assert rec["inferred_effect_classes"] == ["vcs_remote"]
    assert rec["inferred_reliable"] is True
    assert rec["effect_classes"] is None  # an inference is never written as the effect


def test_a_tool_other_than_bash_has_no_inference():
    rec = dr.build(tool_name="Edit", tool_input={"file_path": "/x"}, decision="allow", rule="pipeline")

    assert rec["inferred_effect_classes"] is None and rec["inferred_reliable"] is None


def _gate():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/hooks/sentinel-gate.py"
    spec = importlib.util.spec_from_file_location("sentinel_gate_for_inference", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "command, classes",
    [
        ("empirica mailbox poll --status accepted --output json", []),
        ("empirica mailbox show prop_x --output json", []),
        ("empirica investigate 'how does x work'", []),
        ("empirica goals-list", []),
        ("empirica mailbox reply --parent-id p --summary s", ["empirica_state"]),
        ("empirica project-bootstrap", ["empirica_state"]),  # stamps retrieval telemetry
        ("empirica finding-log --finding x", ["empirica_state"]),
    ],
)
def test_the_gate_s_read_check_knows_the_reads_whose_names_do_not_say_so(command, classes):
    """ecodex measured 57 of 310 empirica_state labels on reads (prop_hcvnottd2bgh3i5qupncxgu3wy)."""
    gate = _gate()
    got = dr.infer_shell_effects(
        command, cwd="/w", project_path="/w", empirica_is_read=gate.empirica_command_only_reads
    )
    assert got["classes"] == classes

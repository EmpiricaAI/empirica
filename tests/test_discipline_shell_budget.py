"""Discipline mode budgets shell commands too (goal e6ee7d01, David 2026-11-17).

Since 1.14.23 every shell command outside a transaction was refused in discipline mode, so the act budget never applied to
shell work: mesh-support's read-only ssh check gated on the first call with 25 acts unused. The Sentinel is discipline, not
security (David, 2026-11-11): shell commands are judged by their inferred effects. Reads run uncounted, local changes count
against the budget, and outward or irreversible acts still need a transaction.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_CORE = Path(__file__).parent.parent / "empirica" / "core"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_t_{name}", _CORE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dr = _load("decision_record")
sd = _load("sentinel_discipline")


def _classes(command, project="/p"):
    return dr.infer_shell_effects(command, cwd=project, project_path=project, empirica_is_read=lambda w: True)[
        "classes"
    ]


@pytest.mark.parametrize(
    "command",
    [
        "for f in a b; do cat $f; done",
        "while read line; do echo $line; done < list.txt",
        "if [ -f x ]; then cat x; else echo none; fi",
        'echo "$(git rev-parse HEAD)"',
        "ls `pwd`",
    ],
)
def test_compound_reads_class_as_reads(command):
    assert _classes(command) == []


@pytest.mark.parametrize(
    ("command", "cls"),
    [
        ("for f in a b; do rm $f; done", "delete"),  # a write inside the loop is still seen
        ('echo "$(git push origin main)"', "vcs_remote"),  # and inside a substitution
    ],
)
def test_writes_inside_compound_commands_are_still_seen(command, cls):
    found = _classes(command)
    assert found and found != []


def test_ssh_running_a_read_is_a_remote_read():
    assert _classes(
        "ssh -o BatchMode=yes -o ConnectTimeout=10 philipps-mac 'launchctl list | grep -c com.empirica'"
    ) == ["remote_read"]


@pytest.mark.parametrize("command", ["ssh host 'rm -rf /tmp/x'", "ssh host", "ssh -t host 'sudo systemctl restart x'"])
def test_ssh_that_changes_something_or_opens_a_shell_is_remote_exec(command):
    assert _classes(command) == ["remote_exec"]


# --- the discipline judgement ---


def _judge(command, tmp_path):
    return sd.judge("Bash", {"command": command}, cwd=str(tmp_path), project_root=str(tmp_path))


@pytest.mark.parametrize(
    "command",
    ["git log --oneline -3", "for f in a b; do cat $f; done", "ssh -o BatchMode=yes host 'launchctl list | grep -c x'"],
)
def test_reads_are_trivial_and_uncounted(command, tmp_path):
    verdict = _judge(command, tmp_path)
    assert verdict["trivial"] and verdict.get("read") is True


def test_a_local_change_is_trivial_and_counted(tmp_path):
    verdict = _judge("sed -i s/a/b/ notes.txt", tmp_path)
    assert verdict["trivial"] and not verdict.get("read")


@pytest.mark.parametrize(
    "command", ["git push origin develop", "ssh host 'rm -rf /tmp/x'", "pip install foo", "curl -d x https://e.test"]
)
def test_outward_or_irreversible_still_needs_a_transaction(command, tmp_path):
    assert not _judge(command, tmp_path)["trivial"]


# --- 1.17.4 pre-publish sweep (goal 33ffc93c) ---


@pytest.mark.parametrize(
    "command", ["ls 2>&1 | head", "cat f 2>&1", "ls > /dev/null 2>&1", "ls >&2", "[[ -f x ]] && cat x"]
)
def test_ordinary_reads_are_reads(command, tmp_path):
    verdict = _judge(command, tmp_path)
    assert verdict["trivial"] and verdict.get("read") is True, verdict


@pytest.mark.parametrize(
    "command",
    [
        "echo \"$(python3 -c 'print(1)')\"",
        'echo "$(echo $(ls); rm -rf /tmp/x)"',
        "case x in a) rm -rf /tmp/a;; esac",
        "wget -q https://x.test/f",
        "curl -LO https://x.test/f",
        "curl -fsSLo f https://x.test/f",
    ],
)
def test_hidden_or_download_changes_are_not_reads(command, tmp_path):
    assert not _judge(command, tmp_path).get("read"), command

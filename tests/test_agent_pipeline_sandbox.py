"""run_action's `--sandbox bwrap` must actually confine writes to the repo.

The sandbox started with `bwrap --bind / /`, a READ-WRITE bind of the whole root, then hid three config directories and re-bound
the repo, so everything else on the machine stayed writable while the docstring said "confines writes to REPO" (cortex, finding
c34a9bb2; it also blocked cortex's action-stage pilot). These tests run a REAL bwrap and try to write, because the claim is about
what the kernel allows, not about the command text. Every claim has a positive control: a sandbox that cannot run at all would
make "the write failed" prove nothing, so the file skips unless a trivial sandboxed command succeeds first.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "empirica/plugins/claude-code-integration/skills/agent-pipeline/scripts/run_action.py"
)

pytestmark = pytest.mark.skipif(shutil.which("bwrap") is None, reason="bwrap not installed")

# A directory that is writable by the user, exists on every Linux, and is NOT /tmp (the sandbox gives /tmp a private tmpfs, so a
# path under it would be invisible and "the write failed" would prove nothing).
OUTSIDE = Path("/var/tmp")


@pytest.fixture(scope="module")
def ra():
    spec = importlib.util.spec_from_file_location("run_action_sandbox", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_action_sandbox"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def box(ra, tmp_path):
    """A repo and a fake home with the files the sandbox expects, plus a runner that executes a shell line inside it."""
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    repo.mkdir()
    for hidden in (".claude", ".empirica", ".config"):
        (home / hidden).mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text('{"login": "kept"}')

    def run(shell: str) -> subprocess.CompletedProcess:
        cmd = [*ra.sandbox_prefix(repo, home=home), "sh", "-c", shell]
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)

    probe = run("true")
    if probe.returncode != 0:
        pytest.skip(f"bwrap cannot run here (no user namespaces?): {probe.stderr.strip()[:120]}")
    return types_namespace(repo=repo, home=home, run=run)


def types_namespace(**kw):
    import types

    return types.SimpleNamespace(**kw)


@pytest.fixture
def outside_file():
    if not os.access(OUTSIDE, os.W_OK):
        pytest.skip(f"{OUTSIDE} is not writable here")
    path = OUTSIDE / f"bwrap-confinement-probe-{uuid.uuid4().hex}"
    yield path
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def test_the_repo_is_writable(box):
    done = box.run(f"echo hi > {box.repo}/inside.txt && cat {box.repo}/inside.txt")
    assert done.returncode == 0 and done.stdout.strip() == "hi"
    assert (box.repo / "inside.txt").read_text().strip() == "hi", "the write must reach the real repo"


def test_a_write_outside_the_repo_is_refused_and_does_not_reach_the_host(box, outside_file):
    done = box.run(f"echo escaped > {outside_file}")
    assert done.returncode != 0, "the sandbox let a process write outside the repo"
    assert not outside_file.exists()
    assert "Read-only file system" in done.stderr or "denied" in done.stderr.lower()


def test_a_directory_cannot_be_created_or_deleted_outside_the_repo(box, outside_file):
    assert box.run(f"mkdir {outside_file}").returncode != 0
    outside_file.write_text("keep me")
    assert box.run(f"rm -f {outside_file}").returncode != 0
    assert outside_file.read_text() == "keep me"


def test_tmp_is_private_to_the_sandbox(box):
    marker = f"/tmp/bwrap-private-{uuid.uuid4().hex}"
    done = box.run(f"echo x > {marker} && test -f {marker}")
    assert done.returncode == 0, "the agent needs a writable /tmp for its tools"
    assert not Path(marker).exists(), "a /tmp write must not reach the host"


def test_the_hidden_config_dirs_are_writable_scratch_that_never_reaches_the_host(box):
    target = box.home / ".claude" / "probe.txt"
    assert box.run(f"echo x > {target} && test -f {target}").returncode == 0
    assert not target.exists(), "writes to the hidden ~/.claude must be thrown away with the sandbox"


def test_the_login_file_stays_readable_and_is_not_writable(box):
    creds = box.home / ".claude" / ".credentials.json"
    assert box.run(f"cat {creds}").stdout.strip() == '{"login": "kept"}'
    assert box.run(f"echo tampered > {creds}").returncode != 0
    assert creds.read_text() == '{"login": "kept"}'


def test_the_other_hidden_dirs_do_not_show_the_hosts_files(box):
    (box.home / ".empirica" / "secret.txt").write_text("host only")
    assert box.run(f"ls {box.home}/.empirica").stdout.strip() == "", "~/.empirica must be hidden behind an empty tmpfs"

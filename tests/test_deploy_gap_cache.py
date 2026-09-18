"""SessionStart reads a cached deploy-gap verdict; it never runs the detector.

The detector (`doctor --deploy-gaps`) costs ~2.1 s, so the hot path reads a
small JSON verdict and spawns a detached refresh when the verdict is missing,
failed, stale or old. Every state that is not "fresh and clean" must say so in
words — a stale clean verdict rendering like a fresh one is the two-states
hazard the cache would otherwise introduce.

Everything here builds its state under tmp_path: the box's real binary, plugin
and checkout are never measured.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

HOOK_LIB = Path(__file__).resolve().parents[1] / "empirica" / "plugins" / "claude-code-integration" / "lib"


@pytest.fixture
def dgc():
    spec = importlib.util.spec_from_file_location("deploy_gap_cache_test", HOOK_LIB / "deploy_gap_cache.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def box(tmp_path, monkeypatch):
    """A fake box: HOME, a git-less project root, an `empirica` binary on a
    private PATH, and a deployed plugin stamp."""
    home = tmp_path / "home"
    root = tmp_path / "proj"
    root.mkdir()
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    binary = binary_dir / "empirica"
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    stamp = home / ".claude" / "plugins" / "local" / "empirica" / ".plugin-version"
    stamp.parent.mkdir(parents=True)
    stamp.write_text("1.0.0")
    monkeypatch.setenv("PATH", str(binary_dir))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    return {"home": home, "root": root, "binary": binary, "stamp": stamp}


def _doctor_json(*checks):
    return {
        "ok": True,
        "summary": {"total": len(checks), "pass": sum(1 for c in checks if c[1] == "PASS")},
        "checks": [{"name": n, "status": s, "detail": d, "hint": h} for (n, s, d, h) in checks],
    }


CLEAN = _doctor_json(("CLI matches checkout", "PASS", "editable", None), ("Deployed plugin fresh", "PASS", "ok", None))
GAPPY = _doctor_json(
    (
        "CLI matches checkout",
        "WARN",
        "pipx copy 1.13.46 while checkout is 1.13.47",
        "pipx install --force --editable .",
    ),
    ("Deployed plugin fresh", "PASS", "ok", None),
)


def _runner_returning(payload, rc=0):
    def run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, rc, stdout=json.dumps(payload) if payload is not None else "", stderr=""
        )

    return run


# ─── unmeasured ───────────────────────────────────────────────────────────


def test_no_cache_says_unmeasured_and_spawns_a_refresh(dgc, box):
    spawned = []
    text = dgc.session_start_block(box["root"], box["home"], spawner=lambda r, h: spawned.append(r) or True)
    assert "not yet measured" in text
    assert "Refreshing in the background" in text
    assert "empirica doctor --deploy-gaps" in text
    assert spawned == [box["root"]]


def test_unmeasured_and_spawn_refused_does_not_claim_refreshing(dgc, box):
    text = dgc.session_start_block(box["root"], box["home"], spawner=lambda r, h: False)
    assert "not yet measured" in text
    assert "Refreshing" not in text


# ─── refresh writes the verdict, including on failure ─────────────────────


def test_refresh_writes_verdict_with_only_non_pass_checks(dgc, box):
    verdict = dgc.refresh(box["root"], box["home"], runner=_runner_returning(GAPPY), clock=lambda: 1000.0)
    on_disk = dgc.read_verdict(box["root"], box["home"])
    assert on_disk == verdict
    assert verdict["error"] is None
    assert verdict["measured_at"] == 1000.0
    assert [a["name"] for a in verdict["attention"]] == ["CLI matches checkout"]
    assert verdict["fingerprint"]["cli"].startswith(str(box["binary"].resolve()))
    assert verdict["fingerprint"]["plugin"].startswith(str(box["stamp"]))


def test_refresh_failure_writes_an_error_verdict_not_nothing(dgc, box):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 90)

    dgc.refresh(box["root"], box["home"], runner=boom, clock=lambda: 1000.0)
    on_disk = dgc.read_verdict(box["root"], box["home"])
    assert on_disk is not None
    assert on_disk["error"].startswith("TimeoutExpired")


def test_refresh_nonzero_exit_is_an_error_verdict(dgc, box):
    dgc.refresh(box["root"], box["home"], runner=_runner_returning(None, rc=1), clock=lambda: 1000.0)
    assert "exited 1" in dgc.read_verdict(box["root"], box["home"])["error"]


# ─── render states ────────────────────────────────────────────────────────


def test_fresh_clean_verdict_renders_nothing(dgc, box):
    dgc.refresh(box["root"], box["home"], runner=_runner_returning(CLEAN), clock=lambda: 1000.0)
    spawned = []
    text = dgc.session_start_block(
        box["root"], box["home"], spawner=lambda r, h: spawned.append(r) or True, clock=lambda: 1300.0
    )
    assert text == ""
    assert spawned == []


def test_fresh_gappy_verdict_renders_each_gap_with_age_and_hint(dgc, box):
    dgc.refresh(box["root"], box["home"], runner=_runner_returning(GAPPY), clock=lambda: 1000.0)
    text = dgc.session_start_block(box["root"], box["home"], spawner=lambda r, h: True, clock=lambda: 1000.0 + 300)
    assert text.startswith("## ⚠️ Deploy gaps on this box — measured 5m ago")
    assert "**WARN** CLI matches checkout: pipx copy 1.13.46" in text
    assert "→ pipx install --force --editable ." in text
    assert "refreshing" not in text.lower()


def test_failed_verdict_says_failed_and_refreshes(dgc, box):
    dgc.refresh(box["root"], box["home"], runner=_runner_returning(None, rc=1), clock=lambda: 1000.0)
    spawned = []
    text = dgc.session_start_block(
        box["root"], box["home"], spawner=lambda r, h: spawned.append(r) or True, clock=lambda: 1060.0
    )
    assert "FAILED" in text
    assert "60s ago" in text
    assert "exited 1" in text
    assert spawned == [box["root"]]


def test_stale_clean_verdict_does_not_render_like_a_fresh_one(dgc, box):
    """The two-states hazard: the binary changed after a clean verdict. Fresh
    clean is silent; stale clean must say the box moved."""
    dgc.refresh(box["root"], box["home"], runner=_runner_returning(CLEAN), clock=lambda: 1000.0)
    box["binary"].write_text("#!/bin/sh\nexit 1\n")  # size changes → stamp changes
    spawned = []
    text = dgc.session_start_block(
        box["root"], box["home"], spawner=lambda r, h: spawned.append(r) or True, clock=lambda: 1100.0
    )
    assert "predates this box" in text
    assert "the empirica binary changed" in text
    assert spawned == [box["root"]]


def test_stale_gappy_verdict_names_what_moved(dgc, box):
    dgc.refresh(box["root"], box["home"], runner=_runner_returning(GAPPY), clock=lambda: 1000.0)
    box["stamp"].write_text("1.0.1")
    text = dgc.session_start_block(box["root"], box["home"], spawner=lambda r, h: True, clock=lambda: 1100.0)
    assert "before the deployed plugin changed" in text
    assert "refreshing" in text
    assert "**WARN** CLI matches checkout" in text


def test_old_verdict_refreshes_even_when_nothing_moved(dgc, box):
    dgc.refresh(box["root"], box["home"], runner=_runner_returning(CLEAN), clock=lambda: 1000.0)
    spawned = []
    text = dgc.session_start_block(
        box["root"],
        box["home"],
        spawner=lambda r, h: spawned.append(r) or True,
        clock=lambda: 1000.0 + dgc.MAX_AGE_SECONDS + 1,
    )
    assert text == ""  # still clean, nothing moved — quiet
    assert spawned == [box["root"]]  # but re-measure


# ─── spawn discipline ─────────────────────────────────────────────────────


def test_spawn_is_detached_and_targets_this_module(dgc, box):
    calls = []

    def popen(cmd, **kw):
        calls.append((cmd, kw))

    assert dgc.spawn_refresh(box["root"], box["home"], popen=popen, clock=lambda: 1000.0) is True
    cmd, kw = calls[0]
    assert cmd[1].endswith("deploy_gap_cache.py")
    assert cmd[2:] == ["--refresh", str(box["root"])]
    assert kw["start_new_session"] is True
    assert kw["stdin"] is subprocess.DEVNULL


def test_second_spawn_within_lock_ttl_is_refused(dgc, box):
    calls = []
    popen = lambda cmd, **kw: calls.append(cmd)  # noqa: E731
    assert dgc.spawn_refresh(box["root"], box["home"], popen=popen, clock=lambda: 1000.0) is True
    assert dgc.spawn_refresh(box["root"], box["home"], popen=popen, clock=lambda: 1000.0 + 5) is False
    assert len(calls) == 1


def test_expired_lock_does_not_block_a_spawn(dgc, box):
    calls = []
    popen = lambda cmd, **kw: calls.append(cmd)  # noqa: E731
    dgc.spawn_refresh(box["root"], box["home"], popen=popen, clock=lambda: 1000.0)
    lock = dgc._lock_path(box["root"], box["home"])
    import os

    os.utime(lock, (1000.0 - 2 * dgc.LOCK_TTL_SECONDS, 1000.0 - 2 * dgc.LOCK_TTL_SECONDS))
    assert dgc.spawn_refresh(box["root"], box["home"], popen=popen, clock=lambda: 1000.0) is True
    assert len(calls) == 2


def test_refresh_child_removes_its_lock(dgc, box, monkeypatch):
    lock = dgc._lock_path(box["root"], None)
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("1")
    monkeypatch.setattr(dgc, "refresh", lambda root: None)
    assert dgc.main(["--refresh", str(box["root"])]) == 0
    assert not lock.exists()


# ─── cache hygiene ────────────────────────────────────────────────────────


def test_corrupt_cache_reads_as_unmeasured(dgc, box):
    p = dgc.cache_path(box["root"], box["home"])
    p.parent.mkdir(parents=True)
    p.write_text("{not json")
    assert dgc.read_verdict(box["root"], box["home"]) is None


def test_cache_is_keyed_per_project_root(dgc, box, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    assert dgc.cache_path(box["root"], box["home"]) != dgc.cache_path(other, box["home"])


# ─── session-init wiring ──────────────────────────────────────────────────


@pytest.fixture
def session_init():
    hook = HOOK_LIB.parent / "hooks" / "session-init.py"
    spec = importlib.util.spec_from_file_location("session_init_dgc_test", hook)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_session_init_surfaces_the_verdict_text(session_init, box, monkeypatch):
    import deploy_gap_cache as live

    monkeypatch.setattr(live, "session_start_block", lambda root: f"VERDICT for {root}")
    assert session_init._deploy_gap_block(box["root"]) == f"VERDICT for {box['root']}"


def test_session_init_reports_a_broken_reader_instead_of_silence(session_init, box, monkeypatch):
    import deploy_gap_cache as live

    def boom(root):
        raise RuntimeError("cache exploded")

    monkeypatch.setattr(live, "session_start_block", boom)
    text = session_init._deploy_gap_block(box["root"])
    assert "unreadable" in text
    assert "cache exploded" in text

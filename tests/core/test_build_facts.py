"""The build a seat reports is the one its own process is running.

Version truth. The first attempt read the build in the heartbeat emitter, which
is the LISTENER DAEMON — a separate, usually newer process — so it described the
daemon and not the session it speaks for. The session records its own build into
its presence record; the daemon forwards that record unchanged.
"""

from __future__ import annotations

from pathlib import Path

from empirica.core import build_facts as bf
from empirica.core.loop_scheduler.practitioner_heartbeat import _practitioner_body


def _pkg(root: Path, name: str, version: str, body: str = "x = 1\n") -> Path:
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (pkg / "mod.py").write_text(body)
    return pkg


def test_the_version_string_does_not_move_the_digest(tmp_path):
    """Correcting a stale __version__ must not read as a code change — that is how
    one measurement said byte-identical and the next said one file differed."""
    a = _pkg(tmp_path / "a", "p", "1.0.0")
    b = _pkg(tmp_path / "b", "p", "9.9.9")
    assert bf.package_content_digest(a) == bf.package_content_digest(b)


def test_real_code_changes_do_move_it(tmp_path):
    """Positive control for the test above: the digest is not simply constant."""
    a = _pkg(tmp_path / "a", "p", "1.0.0")
    b = _pkg(tmp_path / "b", "p", "1.0.0", body="x = 2\n")
    assert bf.package_content_digest(a) != bf.package_content_digest(b)


def test_a_moved_module_counts_as_a_change(tmp_path):
    a = _pkg(tmp_path / "a", "p", "1.0.0")
    b = _pkg(tmp_path / "b", "p", "1.0.0")
    (b / "mod.py").rename(b / "renamed.py")
    assert bf.package_content_digest(a) != bf.package_content_digest(b)


def test_an_unreadable_package_is_None_not_a_match(tmp_path):
    """ "Could not measure" must never read as "same"."""
    assert bf.package_content_digest(tmp_path / "missing") is None
    (tmp_path / "empty").mkdir()
    assert bf.package_content_digest(tmp_path / "empty") is None


def test_in_process_build_names_its_source_and_carries_both_version_strings():
    facts = bf.in_process_build()
    assert facts["source"] == "in_process"
    assert set(facts) >= {"version", "dist_version", "version_disagrees", "path", "digest"}
    # Neither string is authoritative, so both are reported and the disagreement
    # is a recorded fact rather than a silent choice between them.
    assert facts["version_disagrees"] is (
        bool(facts["version"] and facts["dist_version"] and facts["version"] != facts["dist_version"])
    )


def test_on_disk_is_a_different_fact_from_in_process(tmp_path):
    pkg = _pkg(tmp_path, "p", "1.0.0")
    disk = bf.on_disk_build(pkg)
    assert disk["source"] == "on_disk"
    assert disk["digest"] == bf.package_content_digest(pkg)
    assert disk["version"] is None, "a path cannot say what a running process imported"


def test_the_heartbeat_forwards_the_sessions_build_unchanged():
    build = {"source": "in_process", "version": "1.13.40", "digest": "abc", "path": "/somewhere"}
    body = _practitioner_body({"claude_session_id": "s1", "build": build}, machine="box")
    assert body["build"] == build, "the daemon carries the session's build, it does not measure one"


def test_a_record_without_a_build_omits_the_field():
    """Omitted, not null: cortex must be able to tell 'not reported' from 'empty'."""
    body = _practitioner_body({"claude_session_id": "s1"}, machine="box")
    assert "build" not in body
    body = _practitioner_body({"claude_session_id": "s1", "build": {}}, machine="box")
    assert "build" not in body

"""Visibility across machines, sequence by sequence (goal 38510fdd, adversarial review of 21b9bfbb7).

Each test is a sequence the review ran against clones of one bare remote. The cause it found: the export judged "changed
here" by comparing the database with this writer's own note, so a tag set by a rebuild read as a local change, or was
missed. Each checkout now keeps a baseline (what it last learned the notes say, at each rebuild and each export); the
export compares the database with it, and the rebuild leaves a row with an unexported change here alone.
"""

from __future__ import annotations

import sqlite3
import subprocess
import time

import pytest

from empirica.cli.command_handlers import sync_commands as sc
from empirica.core.canonical.empirica_git import graph_note_export as gx

X = "ab000000-0000-0000-0000-00000000000x"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)


class Machine:
    def __init__(self, root, remote):
        self.root = root
        _git(root.parent, "clone", "-q", str(remote), root.name)
        (root / ".empirica").mkdir(exist_ok=True)
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY, visibility TEXT)")
        self.conn.execute("INSERT INTO project_findings VALUES (?, 'local')", (X,))

    def tag(self, tier):
        self.conn.execute("UPDATE project_findings SET visibility = ? WHERE id = ?", (tier, X))
        time.sleep(0.002)

    def tier(self):
        return self.conn.execute("SELECT visibility FROM project_findings WHERE id = ?", (X,)).fetchone()[0]

    def export(self):
        gx.export_visibility_notes(self.conn, str(self.root))
        time.sleep(0.002)

    def push(self, monkeypatch):
        monkeypatch.setattr(sc, "_get_workspace_root", lambda: str(self.root))
        specs = [s for b in (sc._note_wildcard_batches("refs/notes/empirica/*:refs/notes/empirica/*") or []) for s in b]
        return _git(self.root, "push", "origin", *specs).returncode if specs else 0

    def pull(self, monkeypatch, rebuild=True):
        monkeypatch.setattr(sc, "_get_workspace_root", lambda: str(self.root))
        rc = _git(self.root, "fetch", "origin", *sc._notes_fetch_refspecs()).returncode
        ok, _error = sc._merge_visibility_notes("origin")
        rc = rc or (0 if ok else 1)
        if rebuild:
            held = {r[0] for r in self.conn.execute("SELECT id FROM project_findings")}
            gx.apply_visibility_notes(self.conn, str(self.root), held_before=held)
        return rc


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    cfg = tmp_path / "gitconfig"
    cfg.write_text("[user]\n\tname = t\n\temail = t@example.invalid\n[commit]\n\tgpgsign = false\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "-q", "--bare", str(remote))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(remote), "seed")
    _git(seed, "commit", "-q", "--allow-empty", "-m", "init")
    _git(seed, "push", "-q", "origin", "HEAD")
    return lambda name: Machine(tmp_path / name, remote)


def test_a_pull_before_postflight_does_not_undo_a_withdrawal(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    a.tag("local")  # withdrawn, not yet exported
    assert a.pull(monkeypatch) == 0
    assert a.tier() == "local"
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    assert b.tier() == "local"


def test_a_copied_withdrawal_does_not_override_the_owners_later_reshare(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    b.export()
    b.push(monkeypatch)
    a.tag("local")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    assert b.tier() == "local"
    a.tag("shared")
    a.export()
    a.push(monkeypatch)  # the owner shares it again
    b.export()
    b.push(monkeypatch)  # b has not pulled the re-share
    a.pull(monkeypatch)
    b.pull(monkeypatch)
    assert a.tier() == "shared" and b.tier() == "shared"


def test_the_owners_reshare_after_a_rebuild_is_recorded(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    b.tag("local")
    b.export()
    b.push(monkeypatch)  # b withdraws
    a.pull(monkeypatch)
    assert a.tier() == "local"
    a.tag("shared")
    a.export()
    a.push(monkeypatch)  # a shares again
    b.pull(monkeypatch)
    assert b.tier() == "shared"


def test_a_withdrawal_before_the_first_export_after_adopting_is_recorded(world, monkeypatch):
    a, c = world("a"), world("c")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    c.pull(monkeypatch)
    assert c.tier() == "shared"
    c.tag("local")
    c.export()
    c.push(monkeypatch)
    a.pull(monkeypatch)
    c.pull(monkeypatch)
    assert a.tier() == "local" and c.tier() == "local"


def test_pull_after_this_writer_exported_is_not_refused(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    a.tag("public")
    a.export()  # exported, not pushed
    b.tag("shared")
    b.export()
    b.push(monkeypatch)
    assert a.pull(monkeypatch) == 0


def test_a_copied_writer_file_gets_a_new_writer(world, monkeypatch):
    a, b = world("a"), world("b")
    a.export()
    (b.root / ".empirica" / gx.WRITER_FILE).write_text((a.root / ".empirica" / gx.WRITER_FILE).read_text())
    assert gx.writer_id(str(b.root)) != gx.writer_id(str(a.root))


def test_on_a_tie_a_withdrawal_wins(world):
    a = world("a")
    gx._write_note(str(a.root), f"{gx.VISIBILITY_PREFIX}/w1/ab", {"tiers": {X: ["shared", 0.0]}})
    gx._write_note(str(a.root), f"{gx.VISIBILITY_PREFIX}/w2/ab", {"tiers": {X: ["local", 0.0]}})
    gx._write_note(str(a.root), f"{gx.VISIBILITY_PREFIX}/w0/ab", {"tiers": {X: ["shared", 0.0]}})
    assert gx.discover_visibility_notes(str(a.root))[X] == "local"


def test_a_rebuild_into_an_emptied_database_applies_the_notes(world, monkeypatch):
    a = world("a")
    a.tag("shared")
    a.export()
    fresh = sqlite3.connect(":memory:")
    fresh.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY, visibility TEXT)")
    fresh.execute("INSERT INTO project_findings VALUES (?, 'local')", (X,))  # re-imported, born local
    gx.apply_visibility_notes(fresh, str(a.root), held_before=set())
    assert fresh.execute("SELECT visibility FROM project_findings").fetchone()[0] == "shared"


# ── second review of the baseline (2cb26a3e1) ───────────────────────────────────────────────────────────────────────────


def test_moving_a_checkout_keeps_its_writer(world, monkeypatch):
    a = world("a")
    a.tag("shared")
    a.export()
    before = gx.writer_id(str(a.root))
    moved = a.root.with_name("a-moved")
    a.root.rename(moved)
    assert gx.writer_id(str(moved)) == before


def test_a_failed_note_write_then_a_rebuild_keeps_the_withdrawal(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    a.tag("local")
    real = gx._write_note
    monkeypatch.setattr(gx, "_write_note", lambda *args, **kw: False)
    a.export()  # the write fails
    monkeypatch.setattr(gx, "_write_note", real)
    a.pull(monkeypatch)
    assert a.tier() == "local"
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    assert b.tier() == "local"


def test_an_interrupted_rebuild_does_not_export_withdrawals(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    b.export()
    gx.rebuild_started(str(b.root))  # a rebuild that stopped before the visibility step
    b.tag("local")  # rows came back untagged
    b.export()
    b.push(monkeypatch)
    a.pull(monkeypatch)
    assert a.tier() == "shared"


def test_worktrees_do_not_refuse_each_others_pull(world, monkeypatch, tmp_path):
    a = world("a")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    _git(a.root, "worktree", "add", "-q", str(tmp_path / "a-wt"), "-b", "wt")
    wt = Machine.__new__(Machine)
    wt.root = tmp_path / "a-wt"
    (wt.root / ".empirica").mkdir(exist_ok=True)
    wt.conn = sqlite3.connect(":memory:")
    wt.conn.execute("CREATE TABLE project_findings (id TEXT PRIMARY KEY, visibility TEXT)")
    wt.conn.execute("INSERT INTO project_findings VALUES (?, 'local')", (X,))
    a.tag("public")
    a.export()  # a's ref is ahead of the remote, in the ref store both worktrees share
    assert wt.pull(monkeypatch) == 0


def test_without_a_baseline_a_pull_does_not_undo_an_unexported_withdrawal(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    a.tag("local")  # withdrawn, not yet exported
    (a.root / ".empirica" / gx.BASELINE_FILE).unlink()
    a.pull(monkeypatch)
    assert a.tier() == "local"


# ── third review (31327c020) ────────────────────────────────────────────────────────────────────────────────────────────


def test_a_retried_rebuild_after_a_crash_restores_the_rows_it_had_added(world, monkeypatch):
    """An emptied database rebuilt from the notes: the crashed rebuild inserted the row untagged; the retry trusts the
    notes for it rather than reading it as a withdrawal."""
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    b.export()
    b.conn.execute("DELETE FROM project_findings")  # the database is emptied
    assert gx.rebuild_started(str(b.root), set()) is None  # a rebuild starts on an empty database ...
    b.conn.execute("INSERT INTO project_findings VALUES (?, 'local')", (X,))  # ... inserts the row untagged, crashes
    held = {r[0] for r in b.conn.execute("SELECT id FROM project_findings")}
    crashed = gx.rebuild_started(str(b.root), held)  # the retry finds the marker
    assert crashed == set()
    gx.apply_visibility_notes(b.conn, str(b.root), held_before=held - (held - crashed))
    assert b.tier() == "shared"


def test_a_withdrawal_made_after_a_crashed_rebuild_survives_the_retry(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    b.export()
    held = {r[0] for r in b.conn.execute("SELECT id FROM project_findings")}
    gx.rebuild_started(str(b.root), held)  # a rebuild crashes; the row existed before it
    b.tag("local")  # the owner withdraws after the crash
    crashed = gx.rebuild_started(str(b.root), held)
    gx.apply_visibility_notes(b.conn, str(b.root), held_before=held - (held - (crashed or set())))
    assert b.tier() == "local"


def test_a_stale_rebuild_marker_is_reported(tmp_path):
    root = tmp_path
    (root / ".empirica").mkdir()
    gx.rebuild_started(str(root))
    assert gx.visibility_health(str(root))["rebuild_pending"] is True


def test_a_checkout_whose_writer_file_got_a_new_inode_still_pushes_its_old_notes(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    a.tag("local")
    a.export()  # withdrawn, not pushed
    writer_file = a.root / ".empirica" / gx.WRITER_FILE
    content = writer_file.read_text()
    writer_file.unlink()
    writer_file.write_text(content)  # restored from backup: same content, new inode
    a.export()
    assert a.push(monkeypatch) == 0
    b.pull(monkeypatch)
    assert b.tier() == "local"


def test_a_fresh_clone_given_this_checkouts_writer_builds_on_its_notes(world, monkeypatch, tmp_path):
    import shutil

    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    fresh = world("a2")
    shutil.copy2(a.root / ".empirica" / gx.WRITER_FILE, fresh.root / ".empirica" / gx.WRITER_FILE)
    shutil.copy2(a.root / ".empirica" / gx.BASELINE_FILE, fresh.root / ".empirica" / gx.BASELINE_FILE)
    monkeypatch.setattr(gx, "_checkout_key", lambda root: json_key(a.root))  # the moved .empirica keeps its binding
    fresh.pull(monkeypatch)
    fresh.tag("local")
    fresh.export()
    assert fresh.push(monkeypatch) == 0
    b.pull(monkeypatch)
    assert b.tier() == "local"


def json_key(root):
    import json

    return json.loads((root / ".empirica" / gx.WRITER_FILE).read_text())["checkout"]


def test_a_diverged_copy_of_another_writers_note_takes_the_remote(world, monkeypatch):
    a, b = world("a"), world("b")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    writer_a = gx.current_writer(str(a.root))
    # b's copy of a's note diverges (a stray local write)
    gx._write_note(str(b.root), f"{gx.VISIBILITY_PREFIX}/{writer_a}/ab", {"tiers": {X: ["shared", 1.0]}})
    a.tag("local")
    a.export()
    a.push(monkeypatch)
    b.pull(monkeypatch)
    assert b.tier() == "local"


def test_doctor_warns_about_a_rebuild_that_never_finished(tmp_path, monkeypatch):
    from empirica.cli.command_handlers import doctor

    (tmp_path / ".empirica").mkdir()
    monkeypatch.setattr(doctor, "_run", lambda cmd: (1, "", ""))
    assert doctor.check_visibility_notes(tmp_path).status == doctor.PASS
    gx.rebuild_started(str(tmp_path))
    check = doctor.check_visibility_notes(tmp_path)
    assert check.status == doctor.WARN and "sync-pull --rebuild" in check.hint


def test_a_copy_pushing_its_originals_older_notes_is_not_a_failed_push(world, monkeypatch):
    from empirica.cli.command_handlers import sync_commands as sc

    a = world("a")
    a.tag("shared")
    a.export()
    a.push(monkeypatch)
    writer_file = a.root / ".empirica" / gx.WRITER_FILE
    content = writer_file.read_text()
    writer_file.unlink()
    writer_file.write_text(content)  # new inode: the next export makes a new writer id; the old one is "previous"
    a.export()
    previous = gx.push_writers(str(a.root))[1]
    gx._write_note(str(a.root), f"{gx.VISIBILITY_PREFIX}/{previous}/ab", {"tiers": {X: ["shared", 5.0]}})  # diverges
    monkeypatch.setattr(sc, "_get_workspace_root", lambda: str(a.root))
    results, errors = sc._push_note_namespaces("origin")
    assert results["empirica/*"], (results, errors)

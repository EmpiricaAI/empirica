"""`projects-unregister --local` also clears registry.yaml and, when signed in, archives on Cortex (goal 2cef87f4).

Found 2026-11-26 removing test-practice-xyz: an entry that lived only in ~/.empirica/registry.yaml was ``not_found`` because the
verb read workspace.db alone, so it needed a hand edit. David asked the same verb to do Cortex's part when Cortex is connected.
The reference refusal (Qdrant collections, sessions...) is unchanged and still holds the registry file back.
"""

from __future__ import annotations

import json
import sqlite3
import time
import types
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml

from empirica.api import registry as reg
from empirica.cli.command_handlers import projects_commands as pc
from empirica.data.repositories.workspace_db import _ensure_workspace_schema


class _Resp:
    def __init__(self, names):
        self._body = json.dumps({"result": {"collections": [{"name": n} for n in names]}}).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def qdrant(monkeypatch):
    import urllib.request

    names: list[str] = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp(names))
    return names


@pytest.fixture
def regfile(tmp_path, monkeypatch):
    path = tmp_path / "registry.yaml"
    monkeypatch.setattr(reg, "DEFAULT_REGISTRY_PATH", path)
    return path


@pytest.fixture
def cortex(monkeypatch):
    """Cortex as the verb sees it: signed in unless ``state['url']`` is None; records every POST."""
    state: dict[str, Any] = {"url": "https://cortex.test", "posts": [], "status": 200, "raise": None}

    monkeypatch.setattr(
        pc, "_resolve_cortex_config", lambda args, org_id=None: (state["url"], "tok" if state["url"] else None)
    )

    def post(url, path, payload, bearer, timeout):
        state["posts"].append((url, path, payload, bearer))
        if state["raise"]:
            raise state["raise"]
        return state["status"], {}

    monkeypatch.setattr(pc, "_post_project", post)
    return state


@pytest.fixture
def ws(tmp_path, monkeypatch, qdrant, regfile, cortex):
    path = tmp_path / "workspace.db"
    monkeypatch.setenv("EMPIRICA_WORKSPACE_DB", str(path))
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _ensure_workspace_schema(conn)
    yield conn
    conn.close()


def _registry(path, *entries):
    rows = [{"project_id": pid, "slug": name, "name": name, "path": f"/tmp/gone/{name}"} for pid, name in entries]
    path.write_text(yaml.safe_dump({"version": 1, "projects": rows}, sort_keys=False))


def _registry_ids(path):
    return [e["project_id"] for e in (yaml.safe_load(path.read_text()) or {}).get("projects", [])]


def _db_project(conn, name):
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO global_projects (id, name, trajectory_path, created_timestamp, updated_timestamp) VALUES (?, ?, ?, ?, ?)",
        (pid, name, f"/tmp/gone/{name}/.empirica", time.time(), time.time()),
    )
    conn.commit()
    return pid


def _run(capsys, ids, **kw):
    base = {"project_id": ",".join(ids), "local": True, "apply": False, "output": "json", "slug": None, "purge": False}
    try:
        code = pc.handle_projects_unregister_command(types.SimpleNamespace(**{**base, **kw})) or 0
    except SystemExit as e:
        code = e.code or 0
    return code, json.loads(capsys.readouterr().out)


def test_a_registry_only_orphan_is_found_and_previewed(ws, regfile, cortex, capsys):
    orphan, keep = str(uuid.uuid4()), str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"), (keep, "keep"))
    code, out = _run(capsys, [orphan])
    assert code == 0 and out["ok"] and not out["applied"]
    row = out["rows"][0]
    assert row["in_registry_yaml"] and not row["in_global_projects"] and row["name"] == "orphan"
    assert _registry_ids(regfile) == [orphan, keep] and cortex["posts"] == []  # a preview writes and sends nothing


def test_apply_removes_the_orphan_only_and_audits_it(ws, regfile, capsys):
    orphan, keep = str(uuid.uuid4()), str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"), (keep, "keep"))
    code, out = _run(capsys, [orphan], apply=True)
    assert code == 0 and out["applied"] and out["removed"] == 1
    assert _registry_ids(regfile) == [keep]
    snap = json.loads(ws.execute("SELECT snapshot FROM registry_audit WHERE entity_id = ?", (orphan,)).fetchone()[0])
    assert [e["name"] for e in snap["registry_yaml"]] == ["orphan"]  # recoverable from the audit row


def test_apply_removes_the_db_row_and_its_registry_entry_together(ws, regfile, capsys):
    pid = _db_project(ws, "both")
    _registry(regfile, (pid, "both"))
    code, out = _run(capsys, [pid], apply=True)
    assert code == 0 and out["removed"] == 1
    assert ws.execute("SELECT COUNT(*) FROM global_projects WHERE id = ?", (pid,)).fetchone()[0] == 0
    assert _registry_ids(regfile) == []


def test_a_referenced_orphan_is_refused_and_the_registry_is_untouched(ws, regfile, qdrant, cortex, capsys):
    orphan = str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"))
    qdrant.append(f"project_{orphan}_findings")
    code, out = _run(capsys, [orphan], apply=True)
    assert code == 1 and not out["ok"] and out["referenced"]
    assert _registry_ids(regfile) == [orphan] and cortex["posts"] == []


def test_an_id_in_no_store_is_still_not_found(ws, regfile, capsys):
    _registry(regfile)
    code, out = _run(capsys, [str(uuid.uuid4())], apply=True)
    assert code == 1 and out["not_found"]


def test_signed_in_apply_archives_on_cortex_softly(ws, regfile, cortex, capsys):
    orphan = str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"))
    code, out = _run(capsys, [orphan], apply=True)
    assert code == 0
    assert cortex["posts"] == [
        ("https://cortex.test", pc.CORTEX_UNREGISTER_PATH, {"project_id": orphan, "purge": False}, "tok")
    ]
    assert out["cortex"][orphan]["outcome"] == "archived"


def test_a_project_cortex_never_had_is_reported_not_failed(ws, regfile, cortex, capsys):
    orphan = str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"))
    cortex["status"] = 404
    code, out = _run(capsys, [orphan], apply=True)
    assert code == 0 and out["cortex"][orphan]["outcome"] == "not_on_cortex"


def test_not_signed_in_stays_local_and_says_so(ws, regfile, cortex, capsys):
    orphan = str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"))
    cortex["url"] = None
    code, out = _run(capsys, [orphan], apply=True)
    assert code == 0 and cortex["posts"] == [] and out["cortex"] == "not signed in: local only"


def test_no_cortex_flag_keeps_it_local(ws, regfile, cortex, capsys):
    orphan = str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"))
    code, out = _run(capsys, [orphan], apply=True, no_cortex=True)
    assert code == 0 and cortex["posts"] == [] and out["cortex"] == "skipped (--no-cortex)"


def test_a_cortex_failure_after_the_local_removal_exits_nonzero(ws, regfile, cortex, capsys):
    orphan = str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"))
    cortex["raise"] = OSError("unreachable")
    code, out = _run(capsys, [orphan], apply=True)
    assert code == 1 and out["applied"] and not out["ok"] and _registry_ids(regfile) == []  # local done, says so
    assert out["cortex"][orphan]["outcome"] == "error" and "unreachable" in out["cortex"][orphan]["error"]


# ── 1.17.20 sweep ───────────────────────────────────────────────────────────────────────────────────────────────────────


def test_no_cortex_without_local_is_refused_not_ignored(ws, cortex, capsys):
    base = {
        "project_id": str(uuid.uuid4()),
        "local": False,
        "apply": False,
        "output": "json",
        "slug": None,
        "purge": False,
    }
    with pytest.raises(SystemExit) as e:
        pc.handle_projects_unregister_command(types.SimpleNamespace(**base, no_cortex=True))
    assert e.value.code == 2 and cortex["posts"] == []


def test_every_duplicate_entry_is_removed_and_kept_in_the_audit(ws, regfile, capsys):
    dup = str(uuid.uuid4())
    rows = [{"project_id": dup, "name": "a", "path": "/x"}, {"project_id": dup, "name": "a", "path": "/z"}]
    regfile.write_text(yaml.safe_dump({"version": 1, "projects": rows}, sort_keys=False))
    code, _ = _run(capsys, [dup], apply=True)
    assert code == 0 and _registry_ids(regfile) == []
    snap = json.loads(ws.execute("SELECT snapshot FROM registry_audit WHERE entity_id = ?", (dup,)).fetchone()[0])
    assert sorted(e["path"] for e in snap["registry_yaml"]) == ["/x", "/z"]


def test_an_entry_added_after_the_plan_survives_the_write(ws, regfile, monkeypatch, capsys):
    orphan, late = str(uuid.uuid4()), str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"))
    real_plan = pc._local_unregister_plan

    def plan_then_another_writer(*a, **k):
        out = real_plan(*a, **k)
        _registry(regfile, (orphan, "orphan"), (late, "late"))  # another writer between the plan and the write
        return out

    monkeypatch.setattr(pc, "_local_unregister_plan", plan_then_another_writer)
    code, out = _run(capsys, [orphan], apply=True)
    assert code == 0 and _registry_ids(regfile) == [late]
    assert out["registry_backup"] and Path(out["registry_backup"]).exists()


def test_a_failed_commit_puts_the_registry_back(ws, regfile, monkeypatch, capsys):
    orphan, keep = str(uuid.uuid4()), str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"), (keep, "keep"))

    class _Conn:
        def __init__(self, real):
            self._real = real

        def commit(self):
            raise sqlite3.OperationalError("disk I/O error")

        def __getattr__(self, name):
            return getattr(self._real, name)

    real_apply = pc._local_unregister_apply
    monkeypatch.setattr(pc, "_local_unregister_apply", lambda conn, *a, **k: real_apply(_Conn(conn), *a, **k))
    code, out = _run(capsys, [orphan], apply=True)
    assert code == 1 and "rolled back" in out["error"]
    assert _registry_ids(regfile) == [orphan, keep]


def test_a_failed_restore_says_so_and_names_the_backup(ws, regfile, monkeypatch, capsys):
    orphan = str(uuid.uuid4())
    _registry(regfile, (orphan, "orphan"))

    class _Conn:
        def __init__(self, real):
            self._real = real

        def commit(self):
            raise sqlite3.OperationalError("disk I/O error")

        def __getattr__(self, name):
            return getattr(self._real, name)

    real_apply = pc._local_unregister_apply
    real_save = reg.save_registry
    calls = {"n": 0}

    def save_then_fail(registry, path=None):
        calls["n"] += 1
        if calls["n"] > 1:
            raise OSError("read-only file system")
        return real_save(registry, path)

    monkeypatch.setattr(reg, "save_registry", save_then_fail)
    monkeypatch.setattr(pc, "_local_unregister_apply", lambda conn, *a, **k: real_apply(_Conn(conn), *a, **k))
    code, out = _run(capsys, [orphan], apply=True)
    assert code == 1 and "could NOT be put back" in out["error"] and ".bak-unregister-" in out["error"]

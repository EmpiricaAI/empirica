"""project-embed that fails does not exit 0.

It failed on every run for fifteen days and no session-end hook noticed. A peer
then observed it print an error and return rc 0. The handler's except block
reports the error and returns None; the dispatcher's fail-closed guard is what
turns that into a nonzero exit, so the two are tested together.
"""

from __future__ import annotations

from argparse import Namespace

from empirica.cli import cli_core, cli_utils
from empirica.cli.command_handlers import project_embed


def _failing_run(monkeypatch, tmp_path):
    import empirica.utils.session_resolver as sr

    # The handler records a failure under the working directory when it died
    # before resolving a project root. Run from tmp_path, or this test writes a
    # false "last run failed" into the developer's checkout, which it did once.
    monkeypatch.chdir(tmp_path)

    def boom(*_a, **_k):
        raise NameError("name 'finding_fact_confidence' is not defined")

    monkeypatch.setattr(sr.InstanceResolver, "project_path", staticmethod(boom))
    monkeypatch.setattr(cli_utils, "_ERROR_REPORTED", [])
    args = Namespace(project_id=None, output="json", verbose=False, global_sync=False)
    return project_embed.handle_project_embed_command(args)


def test_the_handler_reports_the_error(monkeypatch, capsys, tmp_path):
    assert _failing_run(monkeypatch, tmp_path) is None
    assert "finding_fact_confidence" in capsys.readouterr().out
    assert cli_utils.errors_reported()


def test_the_dispatcher_turns_a_reported_error_into_a_nonzero_exit(monkeypatch, tmp_path):
    result = _failing_run(monkeypatch, tmp_path)
    assert cli_core._handle_command_result(result, "project-embed") == 1


def test_positive_control_a_clean_none_still_exits_zero(monkeypatch):
    monkeypatch.setattr(cli_utils, "_ERROR_REPORTED", [])
    assert cli_core._handle_command_result(None, "project-embed") == 0


# --- the outcome is recorded, because the session-end launch has no audience ---


def _project(tmp_path):
    (tmp_path / ".empirica" / "sessions").mkdir(parents=True)
    return tmp_path


def test_a_failed_run_leaves_a_record_doctor_fails_on(tmp_path, monkeypatch):
    import json

    from empirica.cli.command_handlers import doctor

    root = _project(tmp_path)
    project_embed.record_embed_outcome(str(root), ok=False, error="NameError: name 'x' is not defined")
    status = json.loads((root / ".empirica" / project_embed.EMBED_STATUS_FILE).read_text())
    assert status["ok"] is False and "NameError" in status["error"]

    check = doctor.check_project_embed_outcome(root)
    assert check.status == doctor.FAIL and "NameError" in check.detail


def test_positive_control_a_good_run_passes(tmp_path):
    from empirica.cli.command_handlers import doctor

    root = _project(tmp_path)
    project_embed.record_embed_outcome(str(root), ok=True, counts={"memory": 3})
    assert doctor.check_project_embed_outcome(root).status == doctor.PASS


def test_no_record_is_a_warning_not_a_pass(tmp_path):
    from empirica.cli.command_handlers import doctor

    assert doctor.check_project_embed_outcome(_project(tmp_path)).status == doctor.WARN


def test_findings_logged_days_after_the_last_run_warn(tmp_path):
    import sqlite3
    import time

    from empirica.cli.command_handlers import doctor

    root = _project(tmp_path)
    project_embed.record_embed_outcome(str(root), ok=True)
    conn = sqlite3.connect(root / ".empirica" / "sessions" / "sessions.db")
    conn.execute("CREATE TABLE project_findings (id TEXT, created_timestamp REAL)")
    conn.execute("INSERT INTO project_findings VALUES ('f1', ?)", (time.time() + 5 * 86400,))
    conn.commit()
    conn.close()
    check = doctor.check_project_embed_outcome(root)
    assert check.status == doctor.WARN and "after the last run" in check.detail


def test_the_handler_records_failure_before_reporting_it(monkeypatch, tmp_path):
    """The except block writes the record even when the run died before resolving a root."""
    import inspect

    source = inspect.getsource(project_embed.handle_project_embed_command)
    failure = source.split("except Exception as e:")[-1]
    assert failure.index("record_embed_outcome(") < failure.index("handle_cli_error(")
    assert "ok=True" in source


def test_an_unwritable_status_does_not_raise(tmp_path):
    project_embed.record_embed_outcome(str(tmp_path / "missing"), ok=True)


def test_a_failing_run_under_test_does_not_touch_the_checkout(monkeypatch, tmp_path):
    from pathlib import Path

    real = Path(__file__).resolve().parent.parent / ".empirica" / project_embed.EMBED_STATUS_FILE
    before = real.stat().st_mtime_ns if real.exists() else None
    _failing_run(monkeypatch, tmp_path)
    after = real.stat().st_mtime_ns if real.exists() else None
    assert before == after


# --- eidetic 0 is what a healthy re-run prints, so the summary says which zero ---


def test_the_summary_separates_new_from_already_present():
    assert project_embed._eidetic_summary(0, 446) == " | eidetic: 0 new, 446 already present"


def test_the_batch_path_records_how_many_were_already_present(monkeypatch):
    """Independent of whether a Qdrant is running: the client and the imports are
    patched at the module the function imports them from. The first version of
    this test passed on a box with Qdrant up and failed in CI, which has none."""
    import empirica.cli.command_handlers.project_embed as pe
    import empirica.core.qdrant.connection as qc

    valid = [({"id": f"f{i}", "finding": f"text {i}"}, f"text {i}", f"h{i}") for i in range(5)]
    monkeypatch.setattr(pe, "_filter_unembedded", lambda client, coll, v: v[:2])  # 2 new of 5
    monkeypatch.setattr(qc, "_get_qdrant_client", lambda *a, **k: object())
    monkeypatch.setattr(qc, "_get_qdrant_imports", lambda: (None, None, None, object))

    def stop(*_a, **_k):
        raise RuntimeError("stop before embedding")

    # Stop at the embedding step: what is under test is the count recorded before it.
    monkeypatch.setattr(qc, "_get_embeddings_batch_for_collection", stop, raising=False)
    monkeypatch.setattr(pe, "_rehydrate_eidetic_sequential", lambda *a, **k: 0)
    pe._rehydrate_eidetic("p", [v[0] for v in valid], lambda **k: True, lambda: True)
    assert pe._LAST_EIDETIC["already_present"] == 3


def test_the_json_result_names_both_numbers():
    import inspect

    assert '"eidetic_already_present"' in inspect.getsource(project_embed.handle_project_embed_command)

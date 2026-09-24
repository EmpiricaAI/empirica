"""A failed bootstrap must not read as a successful one.

Measured by running it: `project-bootstrap` printed `ok: false` and exited 0,
and one path printed a bare line with no JSON at all. `project-switch` gated on
that return code, got 0, and on a parse error filled in `{"ok": True, "note":
"bootstrap ran but non-JSON output"}` — so a bootstrap that printed "Project not
found" was reported to the caller as having loaded the project context.
"""

from __future__ import annotations

from empirica.cli.command_handlers.project_bootstrap import _bootstrap_error_output
from empirica.cli.command_handlers.project_commands import _parse_bootstrap_output


class _Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_the_error_helper_returns_a_failing_exit_code(capsys):
    code = _bootstrap_error_output("json", "no project here", "run project-init")
    assert code == 1, "the handler returns this value; None made every failure exit 0"
    assert '"ok": false' in capsys.readouterr().out


def test_a_verdict_is_read_from_the_payload_not_the_return_code():
    """Exit 0 with ok:false is exactly the shape that caused this."""
    r = _Result(stdout='{"ok": false, "error": "Project not found: abc"}', returncode=0)
    assert _parse_bootstrap_output(r)["ok"] is False


def test_json_followed_by_other_output_still_parses():
    """The real failure mode: valid JSON, then a further line, so a whole-stdout
    json.loads raises on output that IS a result."""
    r = _Result(stdout='{"ok": true, "project_id": "p"}\n❌ something else\n')
    assert _parse_bootstrap_output(r)["ok"] is True


def test_unparseable_output_is_a_failure_and_carries_what_was_printed():
    r = _Result(stdout="❌ Project not found: abc", returncode=0)
    out = _parse_bootstrap_output(r)
    assert out["ok"] is False, "inventing ok:true here is what hid the failure"
    assert "Project not found" in out["stdout_head"]


def test_empty_output_is_a_failure_too():
    assert _parse_bootstrap_output(_Result())["ok"] is False

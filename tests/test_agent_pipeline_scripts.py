"""The agent-pipeline skill's mechanical checks: pointer validation, verbatim quotes, the base-test runner, the action runner.

Each check exists to refuse something a model would let through, so every refusal below is a negative control set beside the
honest input that must pass. The runners are tested with the interpreter running the suite and a stub in place of `claude`,
inside tmp_path only: nothing here depends on the box (no PATH binaries, no real home, no sandbox tool).
"""

from __future__ import annotations

import importlib.util
import json
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import jsonschema
import pytest

SKILL = Path(__file__).resolve().parents[1] / "empirica/plugins/claude-code-integration/skills/agent-pipeline"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def vp():
    return _load("validate_pointers")


@pytest.fixture(scope="module")
def ctq():
    return _load("check_tag_quotes")


@pytest.fixture(scope="module")
def rbt():
    return _load("run_base_tests")


@pytest.fixture(scope="module")
def ra():
    return _load("run_action")


# ---- validate_pointers ----------------------------------------------------------------------------------------------------


LINES = {"pkg/a.py": 100, "pkg/b.py": 40}


def _p(file="pkg/a.py", start=10, end=30, **kw):
    return {"file": file, "start": start, "end": end, "reason": "other", "note": "n", **kw}


def test_valid_pointer_passes_and_coverage_counts_unique_lines(vp):
    out = vp.validate([_p(start=10, end=30), _p(start=20, end=40)], LINES)
    assert len(out["valid"]) == 2 and out["dropped"] == []
    assert out["covered_lines"] == 31  # 10..40, overlap counted once
    assert out["total_lines"] == 140


@pytest.mark.parametrize(
    ("pointer", "reason"),
    [
        (_p(file="pkg/ghost.py"), "unknown_file"),
        (_p(start=0, end=5), "bad_range"),
        (_p(start=30, end=10), "bad_range"),
        (_p(start=90, end=140), "beyond_file"),
        (_p(start=1, end=100), "too_long"),
    ],
)
def test_each_invalid_pointer_is_dropped_with_its_reason(vp, pointer, reason):
    out = vp.validate([pointer], LINES, max_span=60)
    assert out["valid"] == [] and [d["reason"] for d in out["dropped"]] == [reason]


def test_a_dropped_pointer_does_not_inflate_coverage(vp):
    out = vp.validate([_p(start=1, end=100), _p(start=10, end=20)], LINES, max_span=60)
    assert out["covered_lines"] == 11


def test_cli_reads_line_counts_from_the_repo(vp, tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("x\n" * 12)
    pointers = tmp_path / "p.json"
    pointers.write_text(json.dumps({"pointers": [_p(start=1, end=12), _p(start=5, end=99)]}))
    assert vp.main([str(pointers), "--root", str(tmp_path), "--files", "pkg/a.py"]) == 0


# ---- check_tag_quotes -----------------------------------------------------------------------------------------------------


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/a.py").write_text("def f(x):\n    return   x +  1\n\n\ndef g():\n    pass\n")
    return tmp_path


def _art(line, quote, file="pkg/a.py", type_="finding"):
    return {"type": type_, "text": "t", "file": file, "line": line, "quote": quote, "severity": "low"}


def test_quote_matches_within_three_lines_and_ignores_whitespace(ctq, tmp_path):
    root = _repo(tmp_path)
    assert ctq.check_quote(_art(2, "return x + 1"), root)
    assert ctq.check_quote(_art(5, "return x + 1"), root)  # line 2 is within 3 of line 5


@pytest.mark.parametrize(
    "art",
    [
        _art(2, "return x + 2"),  # forged
        _art(6, "return x + 1"),  # real text, wrong place (4 lines away)
        _art(2, "return x + 1", file="pkg/ghost.py"),  # missing file
        _art(500, "return x + 1"),  # line beyond the file
        _art(2, ""),  # empty quote proves nothing
    ],
)
def test_forged_or_misplaced_quotes_are_rejected(ctq, tmp_path, art):
    assert not ctq.check_quote(art, _repo(tmp_path))


def test_report_counts_by_type_and_lists_the_invalid(ctq, tmp_path):
    root = _repo(tmp_path)
    tag = {"artifacts": [_art(2, "return x + 1"), _art(2, "nope", type_="unknown")]}
    rep = ctq.check(tag, root)
    assert rep["artifacts"] == 2 and rep["valid"] == 1
    assert rep["by_type"] == {"finding": [1, 1], "unknown": [0, 1]}
    assert [i["index"] for i in rep["invalid"]] == [1]


# ---- run_base_tests -------------------------------------------------------------------------------------------------------


def _pkg_repo(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg/__init__.py").write_text("")
    (tmp_path / "pkg/mod.py").write_text("def double(x):\n    return x + x\n\n\ndef broken_inc(x):\n    return x\n")
    (tmp_path / "tests").mkdir()
    return tmp_path


def _runner_kwargs(repo: Path) -> dict:
    return {"repo": repo, "python": sys.executable, "wrap": [], "import_probe": "pkg"}


def test_classify_separates_defect_from_environment(rbt):
    assert rbt.classify(0, "1 passed") == "passes_on_base"
    assert rbt.classify(1, "E   AssertionError: assert 1 == 2") == "reproduced"
    assert rbt.classify(1, "E   ValueError: not a pid") == "broken"
    assert rbt.classify(2, "ERROR collecting") == "broken"
    assert rbt.classify("timeout", "") == "broken"


def test_controls_classify_correctly_on_a_working_interpreter(rbt, tmp_path):
    repo = _pkg_repo(tmp_path)
    got = rbt.run_controls(**_runner_kwargs(repo))
    assert got == {"pass": "passes_on_base", "fail": "reproduced", "broken": "broken"}


def test_controls_abort_when_the_interpreter_cannot_import_the_project(rbt, tmp_path):
    """The failure that cost an experiment: a wrong interpreter makes every test 'broken'. The controls must refuse to go on."""
    repo = _pkg_repo(tmp_path)
    kwargs = _runner_kwargs(repo) | {"import_probe": "no_such_package_for_controls"}
    with pytest.raises(RuntimeError, match="controls"):
        rbt.run_controls(**kwargs)


def test_run_all_reproduces_a_real_defect_and_leaves_no_test_file_behind(rbt, tmp_path):
    repo = _pkg_repo(tmp_path)
    tasks = [
        {
            "id": "t0",
            "test_path": "tests/test_pipe_t0.py",
            "test_source": "from pkg.mod import broken_inc\n\ndef test_inc():\n    assert broken_inc(1) == 2\n",
        },
        {
            "id": "t1",
            "test_path": "tests/test_pipe_t1.py",
            "test_source": "from pkg.mod import double\n\ndef test_double():\n    assert double(2) == 4\n",
        },
        {
            "id": "t2",
            "test_path": "tests/test_pipe_t2.py",
            "test_source": "from pkg.mod import nope\n\ndef test_x():\n    pass\n",
        },
    ]
    rows = rbt.run_all(tasks, **_runner_kwargs(repo))
    assert {r["id"]: r["class"] for r in rows} == {"t0": "reproduced", "t1": "passes_on_base", "t2": "broken"}
    assert list((repo / "tests").glob("test_pipe_*.py")) == []


def test_tasks_are_read_from_a_tagger_output_a_list_of_them_or_a_flat_list(rbt):
    one = {"unit": "U1", "tasks": [{"test_path": "tests/a.py", "test_source": "s"}]}
    assert [t["id"] for t in rbt.tasks_from(one)] == ["U1:0"]
    assert [
        t["id"] for t in rbt.tasks_from([one, {"unit": "U2", "tasks": [{"test_path": "p", "test_source": "s"}]}])
    ] == ["U1:0", "U2:0"]
    assert [t["id"] for t in rbt.tasks_from([{"id": "x", "test_path": "p", "test_source": "s"}])] == ["x"]


# ---- run_action -----------------------------------------------------------------------------------------------------------


def _git_repo(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    _pkg_repo(repo)
    (repo / "tests/test_existing.py").write_text(
        "from pkg.mod import double\n\ndef test_existing():\n    assert double(3) == 6\n"
    )
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"]):
        subprocess.run(["git", *cmd], cwd=repo, check=True, capture_output=True)
    return repo


def _stub(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "claude_stub.py"
    path.write_text(
        f"#!{sys.executable}\nimport json, pathlib\n"
        + textwrap.dedent(body)
        + "\nprint(json.dumps({'type': 'result', 'subtype': 'success', 'total_cost_usd': 0.01, 'num_turns': 1, 'result': 'done'}))\n"
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


HIDDEN = {
    "test_path": "tests/test_pipe_hidden.py",
    "test_source": "from pkg.mod import broken_inc\n\ndef test_inc():\n    assert broken_inc(1) == 2\n",
}


def _act(ra, root, stub, scope=("pkg/mod.py",), repo=None):
    repo = repo or _git_repo(root)
    meta = ra.run_action(
        repo=repo,
        prompt="fix it",
        model="m",
        budget=1.0,
        scope=list(scope),
        out=root / "out",
        python=sys.executable,
        claude_bin=str(stub),
        sandbox="none",
        timeout=60,
        **HIDDEN,
    )
    return repo, meta


FIX = "p = pathlib.Path('pkg/mod.py'); p.write_text(p.read_text().replace('def broken_inc(x):\\n    return x', 'def broken_inc(x):\\n    return x + 1'))"


def test_a_real_fix_succeeds_and_the_hidden_test_never_stays_in_the_repo(ra, tmp_path):
    repo, meta = _act(ra, tmp_path, _stub(tmp_path, FIX))
    assert (
        meta["success"] is True
        and meta["hidden"]["exit"] == 0
        and meta["regressions"] == []
        and meta["outside_scope"] == []
    )
    assert not (repo / HIDDEN["test_path"]).exists()
    assert (tmp_path / "out/meta.json").exists() and "broken_inc" in (tmp_path / "out/diff.patch").read_text()


def test_no_change_fails_the_hidden_test(ra, tmp_path):
    _, meta = _act(ra, tmp_path, _stub(tmp_path, "pass"))
    assert meta["success"] is False and meta["hidden"]["class"] == "reproduced"


def test_a_comment_only_change_is_not_a_fix(ra, tmp_path):
    """The planted no-op: it touches a file in scope and still must not count."""
    stub = _stub(tmp_path, "p = pathlib.Path('pkg/mod.py'); p.write_text('# fixed\\n' + p.read_text())")
    _, meta = _act(ra, tmp_path, stub)
    assert meta["success"] is False and meta["files_changed"] == ["pkg/mod.py"]


def test_a_fix_that_breaks_an_existing_test_is_a_regression(ra, tmp_path):
    stub = _stub(
        tmp_path,
        "p = pathlib.Path('pkg/mod.py'); s = p.read_text().replace('return x + x', 'return x * 3').replace('def broken_inc(x):\\n    return x', 'def broken_inc(x):\\n    return x + 1'); p.write_text(s)",
    )
    _, meta = _act(ra, tmp_path, stub)
    assert meta["hidden"]["exit"] == 0 and meta["regressions"] and meta["success"] is False


def test_a_failure_that_already_existed_is_not_a_regression(ra, tmp_path):
    repo = _git_repo(tmp_path)
    (repo / "tests/test_old_failure.py").write_text(
        "from pkg.mod import double\n\ndef test_old():\n    assert double(1) == 99\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "old failure"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    _, meta = _act(ra, tmp_path, _stub(tmp_path, FIX), repo=repo)
    assert meta["regressions"] == [] and meta["success"] is True


def test_untracked_files_are_listed_and_in_scope_edits_are_not_flagged(ra, tmp_path):
    stub = _stub(tmp_path, FIX + "; pathlib.Path('pkg/extra.py').write_text('x = 1\\n')")
    _, meta = _act(ra, tmp_path, stub)
    assert meta["outside_scope"] == [] and meta["files_untracked"] == ["pkg/extra.py"] and meta["success"] is True


def test_an_edit_outside_the_stated_scope_is_flagged(ra, tmp_path):
    _, meta = _act(ra, tmp_path, _stub(tmp_path, FIX + "; pathlib.Path('pkg/__init__.py').write_text('y = 2\\n')"))
    assert meta["outside_scope"] == ["pkg/__init__.py"]


def test_sandbox_command_hides_the_real_config_and_names_the_interpreter_path(ra, tmp_path):
    cmd = ra.sandbox_prefix(tmp_path / "repo", home=tmp_path / "home", venv_bin="/opt/venv/bin")
    joined = " ".join(map(str, cmd))
    assert cmd[0] == "bwrap" and "--die-with-parent" in cmd
    for hidden in (".claude", ".empirica", ".config"):
        assert f"--tmpfs {tmp_path / 'home' / hidden}" in joined
    assert "PATH=/opt/venv/bin:" in joined
    assert ra.sandbox_prefix(tmp_path / "repo", home=tmp_path / "home", venv_bin=None) != []
    assert ra.build_command(
        sandbox="none",
        repo=tmp_path,
        home=tmp_path,
        claude_bin="claude",
        model="m",
        prompt="p",
        budget=1.0,
        venv_bin=None,
    )[:2] == ["claude", "-p"]


# ---- schemas --------------------------------------------------------------------------------------------------------------


def test_the_shipped_schemas_are_valid_and_accept_a_real_shape():
    tag = json.loads((SKILL / "schemas/tag.schema.json").read_text())
    verdict = json.loads((SKILL / "schemas/verdict.schema.json").read_text())
    jsonschema.Draft202012Validator.check_schema(tag)
    jsonschema.Draft202012Validator.check_schema(verdict)
    good_tag = {
        "unit": "U1",
        "summary": "s",
        "artifacts": [{"type": "finding", "text": "t", "file": "a.py", "line": 3, "quote": "q", "severity": "low"}],
        "tasks": [
            {
                "artifact_index": 0,
                "objective": "o",
                "steps": ["a"],
                "file_scope": ["a.py"],
                "effort": "S",
                "test_path": "tests/test_x.py",
                "test_source": "s",
            }
        ],
        "proposals": [
            {
                "objective": "o",
                "question": "q",
                "predicted_answer": "a",
                "reason": "r",
                "options": [{"label": "l", "consequence": "c"}, {"label": "m", "consequence": "d"}],
            }
        ],
        "regions_read": [{"file": "a.py", "start": 1, "end": 9}],
    }
    jsonschema.validate(good_tag, tag)
    bad = json.loads(json.dumps(good_tag))
    bad["artifacts"][0]["type"] = "mistake"  # a bug in code is a finding; mistakes are about the agent
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, tag)
    jsonschema.validate(
        {
            "unit": "U1",
            "verdicts": [
                {
                    "id": "U1-F01",
                    "verdict": "refuted",
                    "severity": "none",
                    "proof_file": "a.py",
                    "proof_line": 1,
                    "proof_quote": "q",
                    "reason": "r",
                }
            ],
        },
        verdict,
    )
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"unit": "U1", "verdicts": [{"id": "x", "verdict": "maybe"}]}, verdict)

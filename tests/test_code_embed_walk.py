"""Which files the code-embed walk indexes (goal 4577152d, the eidetic-crowding report).

`embed_project_code` filtered `any(ex in str(f) for ex in exclude)` over the ABSOLUTE path, as substrings. Two consequences, both measured:
a copy under `build/lib/` (setuptools leaves one, and `.gitignore` hides it from every tool except this glob) was indexed as module
`build.lib.empirica...`, a duplicate of the live module that came back in `project-search` beside it; and any file whose path merely CONTAINED an
excluded word was dropped, such as `my_venv_tool.py`, or EVERYTHING when the project root itself sat under a directory named `venv`. The walk now
matches whole path components relative to the root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from empirica.core.qdrant import code_embeddings as ce

SOURCE = "def public_thing(x):\n    '''Do a thing.'''\n    return x\n"


def _walk(root: Path, monkeypatch) -> set[str]:
    seen: list[str] = []
    monkeypatch.setattr(ce, "embed_code_api", lambda _project_id, info: seen.append(info["module_path"]) or True)
    result = ce.embed_project_code("p", root)
    assert result["errors"] == 0
    return set(seen)


def _write(root: Path, *rels: str) -> None:
    for rel in rels:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SOURCE)


def test_a_setuptools_build_copy_is_not_indexed_beside_the_live_module(tmp_path, monkeypatch):
    _write(tmp_path, "pkg/mod.py", "build/lib/pkg/mod.py", "build/bdist.linux-x86_64/pkg/mod.py")
    assert _walk(tmp_path, monkeypatch) == {"pkg.mod"}


@pytest.mark.parametrize(
    "junk", ["node_modules/x/y.py", ".venv/lib/z.py", "venv/lib/z.py", "__pycache__/q.py", ".git/hooks/h.py"]
)
def test_the_directories_that_were_always_excluded_still_are(tmp_path, monkeypatch, junk):
    _write(tmp_path, "pkg/mod.py", junk)
    assert _walk(tmp_path, monkeypatch) == {"pkg.mod"}


def test_a_file_that_only_contains_an_excluded_word_is_indexed(tmp_path, monkeypatch):
    _write(tmp_path, "tools/my_venv_tool.py", "tools/git_helpers.py", "tools/build_facts.py")
    assert _walk(tmp_path, monkeypatch) == {"tools.my_venv_tool", "tools.git_helpers", "tools.build_facts"}


def test_a_project_that_lives_under_a_directory_named_venv_is_not_excluded_wholesale(tmp_path, monkeypatch):
    root = tmp_path / "venv" / "proj"
    _write(root, "pkg/mod.py")
    assert _walk(root, monkeypatch) == {"pkg.mod"}


def test_a_source_directory_that_happens_to_be_called_build_is_kept(tmp_path, monkeypatch):
    """Only the setuptools layout (build/lib, build/bdist.*) is an artefact; a package named build is source."""
    _write(tmp_path, "src/build/runner.py")
    assert _walk(tmp_path, monkeypatch) == {"src.build.runner"}


@pytest.mark.parametrize(
    "junk",
    [
        ".venv-311/lib/z.py",
        "venv3/b.py",
        "Venv/d.py",
        ".venv_old/x.py",
        "site-packages/pkg/m.py",
        ".tox/py311/x.py",
        "pkg.egg-info/x.py",
    ],
)
def test_virtualenv_like_and_packaging_directories_stay_excluded(tmp_path, monkeypatch, junk):
    """The old substring test dropped `.venv-311` and `venv3`; an exact-component match let them in and embedded site-packages as code_api (sweep S2)."""
    _write(tmp_path, "pkg/mod.py", junk)
    assert _walk(tmp_path, monkeypatch) == {"pkg.mod"}


def test_a_file_or_directory_that_merely_contains_venv_in_a_longer_word_is_kept(tmp_path, monkeypatch):
    _write(tmp_path, "tools/venvtool/runner.py", "tools/inventory/x.py", "tools/my_venv_tool.py")
    assert _walk(tmp_path, monkeypatch) == {"tools.venvtool.runner", "tools.inventory.x", "tools.my_venv_tool"}

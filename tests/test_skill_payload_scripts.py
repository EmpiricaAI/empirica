"""Scripts that ship inside a skill must parse, need no install, and actually run.

Skill directories are distribution payload, and some of them now carry more than a
SKILL.md — `epistemic-editing` ships a 249-line renderer vendored from empirica-paper.
That content is excluded from ruff on purpose (see the note in pyproject.toml): it is
another practice's source, and reformatting it would conflict on every upstream sync.

An exclude with nothing behind it is a silent hole — the file could stop parsing and
nothing in CI would notice. So these tests replace the coverage that was dropped, and
they check the properties that actually matter for payload rather than its style:

  1. it parses at the Python version we ship for,
  2. it imports only the standard library, because the skill promises "no install"
     and a user who has to `pip install` something is a user for whom it is broken,
  3. a script the skill locates *in its own directory* really is there, and nothing
     ships that the skill's docs never mention,
  4. it runs — on a document it did not ship with.

(3) is deliberately narrow. A skill also names repo paths and worked examples
(`src/auth.py`, `core/x.py`) and files the *user* creates (`flags.json`), so a check on
every `.py` token fires on skills that are perfectly fine — 8 of 18, when this test was
first written. What is checkable is the `<skill-dir>/name.py` form, which is a skill
asserting the file is bundled.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _ROOT / "empirica" / "plugins" / "claude-code-integration" / "skills"


def _payload_scripts() -> list[Path]:
    return sorted(p for p in _SKILLS_DIR.rglob("*.py") if "__pycache__" not in p.parts)


def _skill_dirs() -> list[Path]:
    return sorted(d for d in _SKILLS_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").exists())


@pytest.mark.parametrize("script", _payload_scripts(), ids=lambda p: p.name)
def test_payload_script_parses(script: Path):
    """A vendored script that stopped parsing would ship broken and silently."""
    ast.parse(script.read_text(encoding="utf-8"), filename=str(script))


@pytest.mark.parametrize("script", _payload_scripts(), ids=lambda p: p.name)
def test_payload_script_imports_stdlib_only(script: Path):
    """`no install` is a promise to the user, and it is checkable."""
    tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    foreign = sorted(m for m in imported if m not in sys.stdlib_module_names)
    assert not foreign, (
        f"{script.relative_to(_ROOT)} imports non-stdlib modules {foreign}. "
        "Skill payload must run on a bare Python — the skill tells the user there is nothing to install."
    )


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_scripts_a_skill_locates_in_its_own_directory_actually_ship(skill_dir: Path):
    """Documented, plausible, unrunnable — the phantom class, one directory down.

    Scoped to `<skill-dir>/name.py`, the way a skill says "this file is here". Bare
    `.py` names are NOT checked: skills legitimately name repo paths and worked
    examples (`src/auth.py`, `core/x.py`), and a check that fires on those fires on
    almost every skill — feedback nobody can act on, which trains dismissal. The
    first draft of this test did exactly that: 8 of 18 skills failed, none broken.
    """
    named: set[str] = set()
    for doc in skill_dir.glob("*.md"):
        named.update(re.findall(r"<skill-dir>/([\w.-]+\.py)", doc.read_text(encoding="utf-8")))

    missing = sorted(n for n in named if not (skill_dir / n).exists())
    assert not missing, f"{skill_dir.name} locates scripts in its directory that it does not ship: {missing}"


@pytest.mark.parametrize("skill_dir", _skill_dirs(), ids=lambda p: p.name)
def test_no_payload_script_ships_unmentioned(skill_dir: Path):
    """The other direction: a script nobody is told to run is dead weight on every install."""
    prose = "\n".join(d.read_text(encoding="utf-8") for d in skill_dir.glob("*.md"))
    orphans = sorted(p.name for p in skill_dir.glob("*.py") if "__pycache__" not in p.parts and p.name not in prose)
    assert not orphans, f"{skill_dir.name} ships scripts its own docs never mention: {orphans}"


def test_galley_renders_a_document_it_did_not_ship_with(tmp_path: Path):
    """The end-to-end the exclude would otherwise leave unguarded.

    Everything the run touches is built under tmp_path — the test must not measure
    whatever happens to be lying around the box.
    """
    galley = _SKILLS_DIR / "epistemic-editing" / "galley.py"
    if not galley.exists():
        pytest.skip("epistemic-editing is not installed in this tree")

    doc = tmp_path / "draft.md"
    doc.write_text(
        "# A draft\n\nEvery practice shows the same sign on every vector.\n\nA second paragraph.\n",
        encoding="utf-8",
    )
    flags = tmp_path / "flags.json"
    flags.write_text(
        json.dumps(
            [
                {
                    "id": "F01",
                    "severity": "blocker",
                    "target": "universal quantifiers",
                    "anchor": "the same sign on every vector",
                    "grounding": "ran",
                    "title": "Thirteen-column claim checked on four columns",
                    "issue": "The loop over X was never run.",
                    "evidence": "rg -n 'every' draft.md",
                    "edit": "Name the four vectors checked, or run the other nine.",
                }
            ]
        ),
        encoding="utf-8",
    )
    out = tmp_path / "galley.html"

    proc = subprocess.run(
        [sys.executable, str(galley), "--md", str(doc), "--flags", str(flags), "--out", str(out)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert proc.returncode == 0, f"galley.py failed: {proc.stderr}"
    assert "unresolved anchors: none" in proc.stdout, (
        f"the anchor did not resolve to a line, so the flag would splice at the wrong paragraph: {proc.stdout}"
    )

    html = out.read_text(encoding="utf-8")
    assert 'id="F01"' in html, "the flag never reached the page"
    assert "Thirteen-column claim" in html, "the flag rendered without its title"
    assert "A second paragraph." in html, "the document itself did not render"

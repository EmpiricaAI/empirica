"""Every shipped skill dir carries a README.md generated from its SKILL.md.

GitHub renders README.md as a directory's front page and does not render
SKILL.md, so a reader arriving from a public link saw a bare file list. The
README is generated (front-matter + first section + pointer), never
hand-edited; this guard keeps it equal to a fresh render.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def gen():
    spec = importlib.util.spec_from_file_location("gen_skill_readmes", REPO / "scripts" / "gen_skill_readmes.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_every_skill_dir_has_a_current_readme(gen):
    dirs = gen.skill_dirs()
    assert len(dirs) >= 19
    stale = [d.name for d in dirs if not (d / "README.md").exists() or (d / "README.md").read_text() != gen.render(d)]
    assert stale == [], f"run scripts/gen_skill_readmes.py: {stale}"


def test_readme_carries_the_skill_description_and_a_pointer(gen):
    d = gen.SKILLS / "reporting-discipline"
    text = gen.render(d)
    assert text.startswith(gen.MARK)
    assert "**When to load:**" in text
    assert "[SKILL.md](SKILL.md)" in text
    assert "/reporting-discipline" in text


def test_front_matter_parser_handles_quoted_and_bare_values(gen):
    fm = gen.front_matter('---\nname: x\ndescription: "a: b, c"\nversion: 1.2.3\n---\n# T\n')
    assert fm == {"name": "x", "description": "a: b, c", "version": "1.2.3"}
    assert gen.front_matter("no front matter") == {}


def test_first_section_is_the_h1_and_its_paragraph(gen):
    title, para = gen.first_section("---\nname: x\n---\n\n# Title\n\nline one\nline two\n\n## Next\nignored\n")
    assert title == "Title"
    assert para == "line one line two"

"""The security corpus ships with the skill that reads it, and each file says what it is and when it was checked.

It used to sit in ``empirica/data/security-corpus/``, outside ``package-data``: absent from every wheel, copied by nothing, with a
README promising a refresh loop that never existed. It now lives in the ``services-auditor`` skill directory, which the wheel
already carries and ``setup-claude-code`` already deploys. These tests hold the properties that make it usable: the location,
the header every file must carry, and that the skill's documented labels exist in the files.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "empirica" / "plugins" / "claude-code-integration" / "skills" / "services-auditor"
CORPUS = SKILL_DIR / "corpus"
FILES = ["owasp-llm-top10.md", "owasp-agentic-top10.md", "nist-ai-rmf.md", "mitre-atlas.md", "google-saif.md"]


def _text(name: str) -> str:
    return (CORPUS / name).read_text(encoding="utf-8")


def test_the_five_files_and_a_readme_are_beside_the_skill():
    assert sorted(p.name for p in CORPUS.glob("*.md")) == sorted([*FILES, "README.md"])


def test_the_old_location_is_gone():
    assert not (ROOT / "empirica" / "data" / "security-corpus").exists()


def test_the_wheel_carries_the_skill_directory_and_so_the_corpus():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"plugins/claude-code-integration/**/*"' in pyproject
    assert CORPUS.is_relative_to(ROOT / "empirica" / "plugins" / "claude-code-integration")


@pytest.mark.parametrize("name", FILES)
def test_every_file_states_its_source_edition_and_verification_date(name):
    text = _text(name)
    assert re.search(r"^\*\*Canonical source:\*\* https?://\S+", text, re.M), f"{name}: no Canonical source"
    assert re.search(r"^\*\*Edition:\*\* \S", text, re.M), f"{name}: no Edition"
    found = re.search(r"^\*\*Verified:\*\* (\d{4}-\d{2}-\d{2}) against https?://", text, re.M)
    assert found, f"{name}: no 'Verified: <date> against <url>'"
    verified = datetime.strptime(found.group(1), "%Y-%m-%d").date()
    assert date(2026, 1, 1) <= verified <= date.today(), f"{name}: Verified date {verified} is implausible"


@pytest.mark.parametrize("name", FILES)
def test_no_file_promises_a_refresh_loop_that_does_not_exist(name):
    text = _text(name)
    assert "Phase 3" not in text and "corpus-refresh loop" not in text
    assert re.search(r"^\*\*Refresh:\*\* manual", text, re.M)


@pytest.mark.parametrize(
    ("name", "pattern", "count"),
    [
        ("owasp-llm-top10.md", r"^## LLM-A(\d{2}) ", 12),
        ("owasp-agentic-top10.md", r"^## Agentic-A(\d{2}) ", 10),
        ("google-saif.md", r"^## SAIF-(\d) ", 6),
    ],
)
def test_the_labels_the_skill_cites_exist_in_the_file(name, pattern, count):
    ids = re.findall(pattern, _text(name), re.M)
    assert len(ids) == count and len(set(ids)) == count


def test_the_skill_names_every_corpus_file_and_the_same_label_ranges():
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    for name in FILES:
        assert f"`{name}`" in skill
    assert (
        "`LLM-A01` … `LLM-A12`" in skill and "`Agentic-A01` … `Agentic-A10`" in skill and "`SAIF-1` … `SAIF-6`" in skill
    )


def test_atlas_uses_atlas_identifiers_not_attack_ones():
    text = _text("mitre-atlas.md")
    assert re.search(r"AML\.T\d{4}", text)
    for line in text.splitlines():
        if line.startswith("## ") or line.startswith("- **"):
            assert not re.match(r"(?:## |- \*\*)T1\d{3}\b", line), f"ATT&CK ID used as an ATLAS ID: {line[:60]}"


def test_nothing_still_points_at_the_old_directory():
    stale = []
    for base in (ROOT / "empirica", ROOT / "docs"):
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in {".py", ".md", ".yaml", ".json", ".toml"}:
                if "empirica/data/security-corpus" in path.read_text(encoding="utf-8", errors="ignore"):
                    stale.append(str(path.relative_to(ROOT)))
    assert [p for p in stale if not p.endswith("acknowledged-stale.json")] == []

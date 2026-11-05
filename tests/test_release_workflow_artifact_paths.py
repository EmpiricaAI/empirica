"""release.yml uploads release assets from the directory it downloaded them into.

On 1.14.21 (the first release with a single artifact) the GitHub release job failed with "no matches found for artifacts/empirica-dist/*":
`download-artifact` given no name puts each artifact in its own subdirectory only when there are several, and flattens a single one into the target
directory. PyPI, Docker and Homebrew had already published, so the release was half done. The job now downloads the artifact by name into the
directory the upload line names, and this test pins that agreement for every artifact directory the file refers to.
"""

from __future__ import annotations

import re
from pathlib import Path

WORKFLOW = Path(__file__).parent.parent / ".github" / "workflows" / "release.yml"


def test_every_artifacts_directory_the_release_uploads_from_is_downloaded_by_name_into_it():
    text = WORKFLOW.read_text()
    used = set(re.findall(r"artifacts/([\w.-]+)/\*", text))
    assert used, "the release job should upload from at least one artifacts/<name>/ directory"
    for name in used:
        assert re.search(rf"name:\s*{re.escape(name)}\s*\n\s*path:\s*artifacts/{re.escape(name)}\b", text), (
            f"release.yml uploads artifacts/{name}/* but does not download the {name} artifact by name into artifacts/{name}"
        )


def test_the_workflow_does_not_download_every_artifact_into_one_directory():
    """The layout of that form depends on how many artifacts exist, which is exactly what changed."""
    assert not re.search(r"download-artifact@v\d+\s*\n\s*with:\s*\n\s*path:\s*artifacts/?\s*\n", WORKFLOW.read_text())

"""serve-service install never pins its WorkingDirectory to a temporary folder (goal 20e5ff77, prop_bgchkdc6).

An installer ran it from a temp folder; the folder was deleted, the unit failed with status=200/CHDIR, the OAuth token
it refreshes expired, and every listener on the machine crash-looped.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from empirica.core.loop_scheduler.persistent_serve import working_directory_for


def test_a_temp_folder_becomes_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert working_directory_for(Path(tempfile.gettempdir()) / "installer-x1") == Path(str(tmp_path / "home"))
    assert working_directory_for("/tmp/abc") == Path(str(tmp_path / "home"))


def test_a_real_folder_is_kept(tmp_path, monkeypatch):
    import empirica.core.loop_scheduler.persistent_serve as ps

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ps, "_temp_roots", lambda: [Path("/nonexistent-tmp")])  # pytest's tmp_path is itself under /tmp
    proj = tmp_path / "proj"
    proj.mkdir()
    assert working_directory_for(proj) == proj.resolve()

"""Tests for the daemon's active-project resolver (v0.5 LOCAL-ARTIFACTS).

The resolver chain:
  1. InstanceResolver.project_path()  — canonical chain (instance_projects → active_work → headless)
  2. CWD walk-up for .empirica/project.yaml — daemon-specific tail
  3. None — daemon starts but per-project endpoints return 503

Per docs/architecture/instance_isolation/: NOT a competing chain. Step 1 IS the
canonical resolver; step 2 is the daemon-specific tail that canonical fails-fast
on by design.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from empirica.api.daemon_project import (
    _read_git_remote,
    _slugify_project_name,
    _walk_up_for_empirica,
    get_cached_daemon_project,
    resolve_daemon_project,
    resolve_for_request,
)


def _make_project(root: Path, name: str, *, project_id: str | None = None, display_name: str | None = None) -> Path:
    """Make a directory with .empirica/project.yaml inside `root`."""
    proj = root / name
    proj.mkdir(parents=True)
    (proj / ".empirica").mkdir()
    yaml_lines = [f"name: {name}\n"]
    if project_id is not None:
        yaml_lines.append(f"project_id: {project_id}\n")
    if display_name is not None:
        yaml_lines.append(f"display_name: {display_name}\n")
    (proj / ".empirica" / "project.yaml").write_text("".join(yaml_lines), encoding="utf-8")
    return proj


# ---------------------------------------------------------------------------
# CWD walk-up
# ---------------------------------------------------------------------------


def test_walk_up_finds_project_at_cwd(tmp_path):
    proj = _make_project(tmp_path, "alpha")
    assert _walk_up_for_empirica(proj) == proj


def test_walk_up_finds_project_from_subdirectory(tmp_path):
    """The whole point of walk-up: works from inside the project tree, not just root."""
    proj = _make_project(tmp_path, "alpha")
    deep = proj / "src" / "nested" / "deep"
    deep.mkdir(parents=True)
    assert _walk_up_for_empirica(deep) == proj


def test_walk_up_returns_none_outside_project(tmp_path):
    """No .empirica/project.yaml anywhere up the tree → None."""
    bare = tmp_path / "no-project-here"
    bare.mkdir()
    assert _walk_up_for_empirica(bare) is None


def test_walk_up_respects_max_depth(tmp_path):
    proj = _make_project(tmp_path, "alpha")
    # Make a very deep subdir; small max_depth should NOT find the project
    deep = proj
    for i in range(10):
        deep = deep / f"level_{i}"
    deep.mkdir(parents=True)
    # max_depth=3 is too small to reach back to proj from level_9
    assert _walk_up_for_empirica(deep, max_depth=3) is None
    # Plenty of depth → finds it
    assert _walk_up_for_empirica(deep, max_depth=20) == proj


def test_walk_up_handles_filesystem_root(tmp_path):
    """Walking up past filesystem root must not loop."""
    # Just confirm no exception when starting from /tmp etc.
    result = _walk_up_for_empirica(Path("/tmp"))
    # Either None (no project there) or some legitimate find — both fine
    assert result is None or (result / ".empirica" / "project.yaml").is_file()


# ---------------------------------------------------------------------------
# resolve_daemon_project — canonical first, CWD walk-up tail
# ---------------------------------------------------------------------------


def test_resolve_uses_canonical_resolver_when_available(tmp_path, monkeypatch):
    """If InstanceResolver.project_path() resolves, the daemon uses that."""
    proj = _make_project(tmp_path, "canonical-wins", project_id="aaaa-bbbb-cccc")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        # Ensure CWD is somewhere ELSE so the walk-up tail wouldn't fire
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PWD", str(tmp_path))
        result = resolve_daemon_project()

    assert result is not None
    assert result["project_path"] == str(proj)
    assert result["project_id"] == "aaaa-bbbb-cccc"
    assert result["project_name"] == "canonical-wins"


def test_resolve_falls_back_to_cwd_walk_up_when_canonical_returns_none(tmp_path, monkeypatch):
    """If canonical returns None, daemon walks up from CWD."""
    proj = _make_project(tmp_path, "cwd-fallback")
    deep = proj / "src" / "deep"
    deep.mkdir(parents=True)

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=None):
        monkeypatch.chdir(deep)
        monkeypatch.setenv("PWD", str(deep))
        result = resolve_daemon_project()

    assert result is not None
    assert result["project_path"] == str(proj)


def test_resolve_returns_none_when_neither_chain_finds_a_project(tmp_path, monkeypatch):
    """No InstanceResolver hit, no .empirica up the tree → None (graceful, not raise)."""
    bare = tmp_path / "outside-any-project"
    bare.mkdir()

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=None):
        monkeypatch.chdir(bare)
        monkeypatch.setenv("PWD", str(bare))
        result = resolve_daemon_project()

    assert result is None


def test_resolve_canonical_path_validated_against_project_yaml(tmp_path, monkeypatch):
    """If canonical returns a path that DOESN'T have .empirica/project.yaml, fall through to CWD walk-up.

    Defensive: a stale instance_projects entry shouldn't trap the daemon.
    """
    proj = _make_project(tmp_path, "real")
    deep = proj / "src"
    deep.mkdir()
    bogus = tmp_path / "stale-no-yaml"
    bogus.mkdir()

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(bogus)):
        monkeypatch.chdir(deep)
        monkeypatch.setenv("PWD", str(deep))
        result = resolve_daemon_project()

    # Should fall through to CWD walk-up since bogus had no project.yaml
    assert result is not None
    assert result["project_path"] == str(proj)


def test_resolve_handles_canonical_resolver_exception_gracefully(tmp_path, monkeypatch):
    """If InstanceResolver throws, fall through to CWD walk-up."""
    proj = _make_project(tmp_path, "graceful")

    def _boom():
        raise RuntimeError("simulated InstanceResolver crash")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", side_effect=_boom):
        monkeypatch.chdir(proj)
        monkeypatch.setenv("PWD", str(proj))
        result = resolve_daemon_project()

    assert result is not None
    assert result["project_path"] == str(proj)


# ---------------------------------------------------------------------------
# project metadata extraction
# ---------------------------------------------------------------------------


def test_resolve_reads_display_name_when_provided(tmp_path, monkeypatch):
    proj = _make_project(tmp_path, "raw-folder", display_name="Pretty Name")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        result = resolve_daemon_project()

    assert result is not None
    assert result["project_name"] == "Pretty Name"
    assert result["project_slug"] == "pretty-name"


def test_resolve_falls_back_to_folder_name_without_display_name(tmp_path, monkeypatch):
    """Folder name = "my-project" → name resolves to "my-project" without display_name."""
    proj = _make_project(tmp_path, "my-project")

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        result = resolve_daemon_project()

    assert result is not None
    assert result["project_name"] == "my-project"


def test_resolve_project_id_is_none_for_local_only_project(tmp_path, monkeypatch):
    """Project without a project_id in yaml → project_id=None (local-only, not on Cortex)."""
    proj = _make_project(tmp_path, "local-only")  # no project_id

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", return_value=str(proj)):
        monkeypatch.chdir(proj)
        result = resolve_daemon_project()

    assert result is not None
    assert result["project_id"] is None
    assert result["project_name"] == "local-only"


# ---------------------------------------------------------------------------
# slugify
# ---------------------------------------------------------------------------


def test_slugify_lowercases_and_hyphenates():
    assert _slugify_project_name("My Project") == "my-project"


def test_slugify_collapses_runs_of_separators():
    assert _slugify_project_name("foo--bar___baz") == "foo-bar-baz"


def test_slugify_strips_leading_trailing_hyphens():
    assert _slugify_project_name("--foo--") == "foo"


def test_slugify_handles_unicode_to_ascii_via_lossy_replacement():
    """Non-ASCII chars become hyphens (lossy but stable)."""
    result = _slugify_project_name("café")
    assert result == "caf"  # é stripped, no trailing hyphen


def test_slugify_falls_back_to_lowercase_when_all_chars_stripped():
    """All-non-ASCII input → fallback to lowercase original (still useful)."""
    result = _slugify_project_name("漢字")
    # Whatever it is, must be a non-empty string
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# git remote
# ---------------------------------------------------------------------------


def test_read_git_remote_returns_none_for_non_git_dir(tmp_path):
    """No git repo → None, not raise."""
    assert _read_git_remote(tmp_path) is None


def test_read_git_remote_normalizes_ssh_form(tmp_path, monkeypatch):
    """git@host:owner/repo.git → https://host/owner/repo."""

    def _fake_run(*args, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="git@github.com:EmpiricaAI/empirica.git\n")

    monkeypatch.setattr("subprocess.run", _fake_run)
    assert _read_git_remote(tmp_path) == "https://github.com/EmpiricaAI/empirica"


def test_read_git_remote_strips_dot_git_from_https(tmp_path, monkeypatch):
    def _fake_run(*args, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="https://github.com/EmpiricaAI/empirica.git\n")

    monkeypatch.setattr("subprocess.run", _fake_run)
    assert _read_git_remote(tmp_path) == "https://github.com/EmpiricaAI/empirica"


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_get_cached_daemon_project_caches_result(tmp_path, monkeypatch):
    """Second call returns cached value without re-resolving."""
    proj = _make_project(tmp_path, "cached")
    call_count = {"n": 0}

    def _counting_canonical():
        call_count["n"] += 1
        return str(proj)

    # Reset cache
    import empirica.api.daemon_project as dp

    dp._cached = False
    dp._cached_project = None

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", side_effect=_counting_canonical):
        monkeypatch.chdir(proj)
        first = get_cached_daemon_project()
        second = get_cached_daemon_project()

    assert first == second
    assert call_count["n"] == 1, "canonical resolver should be called only once due to caching"


def test_get_cached_daemon_project_refresh_forces_re_resolve(tmp_path, monkeypatch):
    proj = _make_project(tmp_path, "refresh-test")
    call_count = {"n": 0}

    def _counting_canonical():
        call_count["n"] += 1
        return str(proj)

    import empirica.api.daemon_project as dp

    dp._cached = False
    dp._cached_project = None

    with patch("empirica.utils.session_resolver.InstanceResolver.project_path", side_effect=_counting_canonical):
        monkeypatch.chdir(proj)
        get_cached_daemon_project()
        get_cached_daemon_project(refresh=True)

    assert call_count["n"] == 2


# ---------------------------------------------------------------------------
# resolve_for_request — registry branch
# ---------------------------------------------------------------------------
#
# Regression coverage for the bug landed 2026-05-13: when an HTTP request
# arrived with ?project_id=<slug>, this function returned the SLUG as
# `project_id` in the resolved dict — but artifact tables in newer projects
# key on the UUID from .empirica/project.yaml. SQL filter
# `WHERE project_id = <slug>` matched zero rows → endpoints returned 200 OK
# with empty payloads. Fix: delegate to _synthesize_project_entry so the
# registry branch resolves UUID consistently with the CWD branch.


def test_resolve_for_request_returns_uuid_project_id_for_uuid_yaml(tmp_path):
    """Registry-lookup with UUID-keyed project.yaml returns UUID as project_id.

    This is the new-project shape (post-2.0): .empirica/project.yaml contains
    a UUID `project_id` matching the projects.id column in artifact tables.
    Pre-fix, this branch returned the registry slug instead — SQL filter
    against UUID-tagged rows matched zero results.
    """
    uuid = "d0317d3b-5e31-480d-9086-26bcd6c4cd1d"
    proj = _make_project(tmp_path, "empirica-cortex", project_id=uuid)

    fake_registry = {
        "projects": [
            {
                "project_id": "empirica-cortex",  # registry uses slug
                "slug": "empirica-cortex",
                "name": "empirica-cortex",
                "path": str(proj),
                "repo_url": "https://github.com/Nubaeon/empirica-cortex",
            }
        ],
    }

    with patch("empirica.api.registry.load_registry", return_value=fake_registry):
        result = resolve_for_request(project_id="empirica-cortex")

    assert result is not None
    # The canonical project_id for DB filtering is the UUID, not the slug.
    assert result["project_id"] == uuid
    # Slug + name + repo_url still carry the human-readable form for routing.
    assert result["project_slug"] == "empirica-cortex"
    assert result["project_name"] == "empirica-cortex"
    assert result["repo_url"] == "https://github.com/Nubaeon/empirica-cortex"
    assert result["project_path"] == str(proj)


def test_resolve_for_request_preserves_registry_metadata_overrides(tmp_path):
    """When registry.yaml has name + slug + repo_url, they win over auto-derived.

    Registry entries are user-curated (via projects-bulk-register). They
    should override project.yaml's defaults for display purposes.
    """
    uuid = "a76ef65b-7784-4956-994a-a0d45badae15"
    proj = _make_project(tmp_path, "ext-folder", project_id=uuid)

    fake_registry = {
        "projects": [
            {
                "project_id": "empirica-extension",
                "slug": "empirica-extension",
                "name": "Empirica Extension",  # human-friendly name
                "path": str(proj),
                "repo_url": "https://github.com/Nubaeon/empirica-extension",
            }
        ],
    }

    with patch("empirica.api.registry.load_registry", return_value=fake_registry):
        result = resolve_for_request(project_id="empirica-extension")

    assert result is not None
    assert result["project_id"] == uuid  # UUID for DB
    assert result["project_slug"] == "empirica-extension"  # registry override
    assert result["project_name"] == "Empirica Extension"  # registry override
    assert result["repo_url"] == "https://github.com/Nubaeon/empirica-extension"


def test_resolve_for_request_slug_yaml_still_resolves(tmp_path):
    """Legacy project.yaml shape (slug-as-project_id) keeps working.

    The CWD branch already handled this via `project_uuid or yaml_id` —
    if no UUID can be looked up from a workspace DB, fall through to the
    yaml's slug-style project_id. The registry branch needs the same
    behavior after delegating to _synthesize_project_entry.
    """
    proj = _make_project(tmp_path, "empirica", project_id="empirica")

    fake_registry = {
        "projects": [
            {
                "project_id": "empirica",
                "slug": "empirica",
                "name": "empirica",
                "path": str(proj),
                "repo_url": None,
            }
        ],
    }

    with patch("empirica.api.registry.load_registry", return_value=fake_registry):
        result = resolve_for_request(project_id="empirica")

    assert result is not None
    # No UUID upgrade available — falls back to yaml's slug-style id.
    # (_resolve_project_uuid returns the workspace-DB UUID when available,
    # else yaml_id. For projects without a workspace DB entry, that's the slug.)
    # In this fixture the yaml_id and the slug are both "empirica" — the set
    # literal previously had two identical values to express "either form is
    # valid", but ruff caught the duplicate (B033). Asserting on the single
    # value preserves the test's intent without the lint trip.
    assert result["project_id"] == "empirica"


def test_resolve_for_request_returns_none_for_unregistered_id(tmp_path):
    """Unknown project_id → None (caller maps to 404)."""
    fake_registry = {"projects": []}

    with patch("empirica.api.registry.load_registry", return_value=fake_registry):
        result = resolve_for_request(project_id="not-in-registry")

    assert result is None


def test_resolve_for_request_returns_none_for_stale_registry_path(tmp_path):
    """Registry points at a path with no .empirica/ → None (stale entry)."""
    stale_path = tmp_path / "deleted-project"
    stale_path.mkdir()  # no .empirica/ subdir

    fake_registry = {
        "projects": [
            {
                "project_id": "stale",
                "slug": "stale",
                "name": "stale",
                "path": str(stale_path),
            }
        ],
    }

    with patch("empirica.api.registry.load_registry", return_value=fake_registry):
        result = resolve_for_request(project_id="stale")

    assert result is None

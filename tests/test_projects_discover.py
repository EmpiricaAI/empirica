"""Tests for projects-discover + projects-list (v0.5 bulk-project-link).

Covers:
  - Filesystem walk discriminator (.empirica/project.yaml is the marker)
  - Sibling-projects-under-bare-parent layout (the common workspace case)
  - SKIP_DIR_NAMES + hidden dirs
  - max_depth ceiling
  - Git remote URL normalization (ssh→https, .git stripping, garbage)
  - Manifest write/read roundtrip
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from empirica.cli.command_handlers.projects_commands import (
    _normalize_remote_url,
    _walk_for_empirica,
    discover_projects,
    filter_projects,
    load_manifest,
    write_manifest,
)

# ---------------------------------------------------------------------------
# Filesystem walk
# ---------------------------------------------------------------------------


def _make_project(root: Path, name: str) -> Path:
    """Create a directory with .empirica/project.yaml inside `root`."""
    proj = root / name
    proj.mkdir(parents=True)
    (proj / ".empirica").mkdir()
    (proj / ".empirica" / "project.yaml").write_text("name: test\n", encoding="utf-8")
    return proj


def test_walk_finds_top_level_project(tmp_path):
    _make_project(tmp_path, "alpha")
    found = _walk_for_empirica(tmp_path / "alpha", max_depth=3, include_hidden=False)
    assert len(found) == 1
    assert found[0].name == "alpha"


def test_walk_finds_sibling_projects_under_bare_parent(tmp_path):
    """The common workspace layout: parent has bare .empirica/ (no project.yaml),
    each sibling subdir has its own real .empirica/project.yaml."""
    # Parent has bare .empirica/ — should NOT count as a project
    (tmp_path / ".empirica").mkdir()
    # Children are real projects
    _make_project(tmp_path, "alpha")
    _make_project(tmp_path, "beta")
    _make_project(tmp_path, "gamma")

    found = _walk_for_empirica(tmp_path, max_depth=3, include_hidden=False)
    names = sorted(p.name for p in found)
    assert names == ["alpha", "beta", "gamma"]


def test_walk_skips_node_modules_and_venv(tmp_path):
    _make_project(tmp_path, "real")
    # Plant a fake project inside node_modules — should be skipped
    nm = tmp_path / "node_modules" / "fake"
    nm.mkdir(parents=True)
    (nm / ".empirica").mkdir()
    (nm / ".empirica" / "project.yaml").write_text("name: fake\n")

    found = _walk_for_empirica(tmp_path, max_depth=5, include_hidden=False)
    names = sorted(p.name for p in found)
    assert names == ["real"]


def test_walk_skips_hidden_dirs_by_default(tmp_path):
    _make_project(tmp_path, "visible")
    # Hidden parent — its children shouldn't be found
    hidden = tmp_path / ".cache" / "hidden_proj"
    hidden.mkdir(parents=True)
    (hidden / ".empirica").mkdir()
    (hidden / ".empirica" / "project.yaml").write_text("name: hidden\n")

    found = _walk_for_empirica(tmp_path, max_depth=5, include_hidden=False)
    names = sorted(p.name for p in found)
    assert names == ["visible"]


def test_walk_finds_hidden_with_include_hidden(tmp_path):
    """include_hidden walks dot-prefixed dirs (but still skips SKIP_DIR_NAMES
    like .cache, .git — those are always-noise regardless of the flag)."""
    hidden = tmp_path / ".myhidden" / "hidden_proj"
    hidden.mkdir(parents=True)
    (hidden / ".empirica").mkdir()
    (hidden / ".empirica" / "project.yaml").write_text("name: hidden\n")

    found = _walk_for_empirica(tmp_path, max_depth=5, include_hidden=True)
    names = sorted(p.name for p in found)
    assert names == ["hidden_proj"]


def test_walk_skips_known_noise_dirs_even_with_include_hidden(tmp_path):
    """Even with include_hidden=True, .cache/.git/.venv etc. are still skipped
    — they're known noise, not just 'hidden'."""
    cached = tmp_path / ".cache" / "shouldnt_find"
    cached.mkdir(parents=True)
    (cached / ".empirica").mkdir()
    (cached / ".empirica" / "project.yaml").write_text("name: cached\n")

    found = _walk_for_empirica(tmp_path, max_depth=5, include_hidden=True)
    assert found == []


def test_walk_does_not_descend_into_dotempirica_dir(tmp_path):
    """Don't search inside .empirica/ for nested 'projects' — that's just data."""
    project = _make_project(tmp_path, "outer")
    # Plant a fake project archive inside .empirica/MOD/
    fake = project / ".empirica" / "MOD" / "embedded"
    fake.mkdir(parents=True)
    (fake / ".empirica").mkdir()
    (fake / ".empirica" / "project.yaml").write_text("name: fake\n")

    found = _walk_for_empirica(tmp_path, max_depth=5, include_hidden=False)
    assert [p.name for p in found] == ["outer"]


def test_walk_respects_max_depth(tmp_path):
    """Project at depth 4 shouldn't be found if max_depth=2."""
    deep = tmp_path / "a" / "b" / "c" / "deep_proj"
    deep.mkdir(parents=True)
    (deep / ".empirica").mkdir()
    (deep / ".empirica" / "project.yaml").write_text("name: deep\n")

    shallow_found = _walk_for_empirica(tmp_path, max_depth=2, include_hidden=False)
    assert shallow_found == []
    deep_found = _walk_for_empirica(tmp_path, max_depth=10, include_hidden=False)
    assert len(deep_found) == 1


def test_walk_returns_empty_for_nonexistent_root(tmp_path):
    assert _walk_for_empirica(tmp_path / "does-not-exist", 5, False) == []


def test_walk_finds_nested_projects_when_outer_is_also_a_project(tmp_path):
    """Nested projects (rare but valid) should both be discovered."""
    outer = _make_project(tmp_path, "outer")
    inner = outer / "subproject"
    inner.mkdir()
    (inner / ".empirica").mkdir()
    (inner / ".empirica" / "project.yaml").write_text("name: inner\n")

    found = _walk_for_empirica(tmp_path, max_depth=5, include_hidden=False)
    names = sorted(p.name for p in found)
    assert names == ["outer", "subproject"]


# ---------------------------------------------------------------------------
# Git remote URL normalization
# ---------------------------------------------------------------------------


def test_normalize_ssh_form_strips_git_suffix():
    assert _normalize_remote_url("git@github.com:EmpiricaAI/empirica.git") == "https://github.com/EmpiricaAI/empirica"


def test_normalize_ssh_form_without_git_suffix():
    assert _normalize_remote_url("git@github.com:EmpiricaAI/empirica") == "https://github.com/EmpiricaAI/empirica"


def test_normalize_https_form_strips_git_suffix():
    assert (
        _normalize_remote_url("https://github.com/EmpiricaAI/empirica.git") == "https://github.com/EmpiricaAI/empirica"
    )


def test_normalize_http_form_passes_through():
    """http (not https) is preserved — some private gitea instances use it."""
    assert _normalize_remote_url("http://git.internal/foo/bar") == "http://git.internal/foo/bar"


def test_normalize_garbage_returns_none():
    assert _normalize_remote_url("not a url") is None
    assert _normalize_remote_url("") is None
    assert _normalize_remote_url("ftp://foo/bar") is None


def test_normalize_handles_whitespace():
    assert _normalize_remote_url("  git@github.com:Foo/Bar.git  ") == "https://github.com/Foo/Bar"


# ---------------------------------------------------------------------------
# discover_projects (high-level)
# ---------------------------------------------------------------------------


def test_discover_projects_returns_manifest_shape(tmp_path):
    _make_project(tmp_path, "alpha")
    _make_project(tmp_path, "beta")

    manifest = discover_projects(roots=[tmp_path], max_depth=3)
    assert "discovered_at" in manifest
    assert "roots" in manifest
    assert "projects" in manifest
    assert len(manifest["projects"]) == 2
    names = sorted(p["name"] for p in manifest["projects"])
    assert names == ["alpha", "beta"]


def test_discover_projects_dedupes_overlapping_roots(tmp_path):
    """If two roots overlap (one is parent of the other), don't double-count."""
    _make_project(tmp_path, "alpha")
    manifest = discover_projects(roots=[tmp_path, tmp_path], max_depth=3)
    assert len(manifest["projects"]) == 1


def test_discover_projects_sorts_results_by_path(tmp_path):
    _make_project(tmp_path, "zebra")
    _make_project(tmp_path, "alpha")
    _make_project(tmp_path, "mango")

    manifest = discover_projects(roots=[tmp_path], max_depth=3)
    paths = [p["path"] for p in manifest["projects"]]
    assert paths == sorted(paths)


def test_discover_projects_includes_repo_url_when_git_remote_set(tmp_path, monkeypatch):
    proj = _make_project(tmp_path, "alpha")

    def fake_run(args, **kwargs):
        class Result:
            returncode = 0
            stdout = "git@github.com:Test/Alpha.git\n"

        return Result()

    with patch("subprocess.run", side_effect=fake_run):
        manifest = discover_projects(roots=[tmp_path], max_depth=3)

    assert manifest["projects"][0]["repo_url"] == "https://github.com/Test/Alpha"
    assert manifest["projects"][0]["git_remote_origin"] == "git@github.com:Test/Alpha.git"
    assert proj.exists()  # sanity: didn't blow up the project


def test_discover_projects_repo_url_none_when_no_git(tmp_path):
    _make_project(tmp_path, "alpha")
    # tmp_path has no git, so git remote get-url origin will fail
    manifest = discover_projects(roots=[tmp_path], max_depth=3)
    assert manifest["projects"][0]["repo_url"] is None


# ---------------------------------------------------------------------------
# Manifest persistence
# ---------------------------------------------------------------------------


def test_write_and_load_manifest_roundtrip(tmp_path):
    manifest = {
        "discovered_at": "2026-05-05T12:00:00+00:00",
        "roots": [str(tmp_path)],
        "projects": [
            {
                "path": "/x/y",
                "name": "y",
                "repo_url": "https://github.com/a/y",
                "has_empirica_dir": True,
                "git_remote_origin": "git@github.com:a/y.git",
            },
        ],
    }
    target = tmp_path / "manifest.yaml"
    write_manifest(manifest, target)

    loaded = load_manifest(target)
    assert loaded == manifest


def test_load_manifest_returns_none_for_missing_file(tmp_path):
    assert load_manifest(tmp_path / "does-not-exist.yaml") is None


def test_load_manifest_returns_none_for_invalid_yaml(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("{ not valid : : yaml :::", encoding="utf-8")
    assert load_manifest(bad) is None


def test_write_manifest_creates_parent_dirs(tmp_path):
    target = tmp_path / "deeply" / "nested" / "manifest.yaml"
    write_manifest({"projects": []}, target)
    assert target.exists()
    loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    assert loaded == {"projects": []}


# ---------------------------------------------------------------------------
# projects-bulk-register (Cortex-dependent)
# ---------------------------------------------------------------------------


from types import SimpleNamespace  # noqa: E402

from empirica.cli.command_handlers.projects_commands import (  # noqa: E402
    _register_one_project,
    _resolve_cortex_config,
)


def test_resolve_cortex_config_prefers_args_over_env(monkeypatch):
    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://env.example.com")
    monkeypatch.setenv("CORTEX_API_KEY", "env-key")
    args = SimpleNamespace(cortex_url="https://flag.example.com", api_key="flag-key")
    url, key = _resolve_cortex_config(args)
    assert url == "https://flag.example.com"
    assert key == "flag-key"


def test_resolve_cortex_config_strips_trailing_slash(monkeypatch, tmp_path):
    # Isolate HOME so the file-first loader (post-2026-05-28 flip) doesn't let
    # the developer's real ~/.empirica/credentials.yaml win over the env under
    # test. With no file, env fills the gap → trailing-slash stripping is what's
    # exercised here. (CI passed without this because CI has no creds file.)
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("EMPIRICA_CREDENTIALS_PATH", raising=False)
    from empirica.config import credentials_loader as cl_mod
    from empirica.config.credentials_loader import CredentialsLoader

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    cl_mod._loader = None

    monkeypatch.setenv("CORTEX_REMOTE_URL", "https://cortex.example.com/")
    monkeypatch.setenv("CORTEX_API_KEY", "key")
    args = SimpleNamespace(cortex_url=None, api_key=None)
    url, _ = _resolve_cortex_config(args)
    assert url == "https://cortex.example.com"


def test_resolve_cortex_config_returns_none_when_unset(monkeypatch, tmp_path):
    # Isolate HOME so the loader doesn't fall through to the developer's
    # real ~/.empirica/credentials.yaml (post-1.9.6 the loader is wired
    # into _resolve_cortex_config via get_credentials_loader()).
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("CORTEX_REMOTE_URL", raising=False)
    monkeypatch.delenv("CORTEX_URL", raising=False)
    monkeypatch.delenv("CORTEX_API_KEY", raising=False)
    monkeypatch.delenv("EMPIRICA_CREDENTIALS_PATH", raising=False)
    # Reset loader singleton + module-level global so it re-reads from
    # the isolated HOME (get_credentials_loader caches a module global).
    from empirica.config import credentials_loader as cl_mod
    from empirica.config.credentials_loader import CredentialsLoader

    CredentialsLoader._instance = None
    CredentialsLoader._credentials_cache = None
    cl_mod._loader = None

    args = SimpleNamespace(cortex_url=None, api_key=None)
    url, key = _resolve_cortex_config(args)
    assert url is None
    assert key is None


def test_register_one_project_success_201():
    """201 Created → outcome=registered, no fallback to admin path.

    Post c27819963: also fires defensive POST /v1/users/me/projects after
    successful register, so call_count == 2 (register + user-link).
    """
    project = {"name": "alpha", "repo_url": "https://github.com/x/alpha"}
    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(201, {"project_id": "uuid-alpha"}),
    ) as m:
        result = _register_one_project(project, "https://cortex", "key", 10.0)
    assert result["outcome"] == "registered"
    assert result["status"] == 201
    # Public path + defensive user-link (no admin fallback on 201)
    assert m.call_count == 2


def test_register_one_project_409_skips_silently():
    project = {"name": "alpha"}
    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(409, {"error": "already exists"}),
    ):
        result = _register_one_project(project, "https://cortex", "key", 10.0)
    assert result["outcome"] == "skipped"
    assert result["reason"] == "already_exists"


def test_register_one_project_404_falls_back_to_admin():
    """404 on /v1/projects/register → retry on /v1/admin/projects.

    Post c27819963: also fires defensive POST /v1/users/me/projects after
    successful admin-path register, so call_count == 3 (register-404 +
    admin-201 + user-link).
    """
    project = {"name": "alpha"}
    responses = iter(
        [
            (404, None),  # /v1/projects/register fails
            (201, {"project_id": "uuid-alpha"}),  # /v1/admin/projects succeeds
            (200, {"linked": True}),  # /v1/users/me/projects link
        ]
    )

    def fake_post(*_args, **_kwargs):
        return next(responses)

    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        side_effect=fake_post,
    ) as m:
        result = _register_one_project(project, "https://cortex", "key", 10.0)
    assert result["outcome"] == "registered"
    assert m.call_count == 3  # register + admin-fallback + user-link


def test_register_one_project_500_fails_without_fallback():
    project = {"name": "alpha"}
    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        return_value=(500, {"error": "internal"}),
    ) as m:
        result = _register_one_project(project, "https://cortex", "key", 10.0)
    assert result["outcome"] == "failed"
    assert result["status"] == 500
    # 500 doesn't trigger fallback — only 404/405 do
    assert m.call_count == 1


def test_register_one_project_network_error_returns_failed():
    """urllib raises URLError → caught and returned as failed result."""
    import urllib.error

    project = {"name": "alpha"}
    with patch(
        "empirica.cli.command_handlers.projects_commands._post_project",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        result = _register_one_project(project, "https://cortex", "key", 10.0)
    assert result["outcome"] == "failed"
    assert "network" in result["reason"]
    assert "URLError" in result["reason"]


# ---------------------------------------------------------------------------
# filter_projects (v0.6 follow-on, surfaced by Cortex)
# ---------------------------------------------------------------------------


_SAMPLE_PROJECTS = [
    {"name": "empirica", "path": "/home/u/work/empirica"},
    {"name": "empirica-extension", "path": "/home/u/work/empirica-extension"},
    {"name": "cortex", "path": "/home/u/work/cortex"},
    {"name": "side-quest", "path": "/home/u/scratch/side-quest"},
    {"name": "old-project", "path": "/home/u/archive/old-project"},
]


def test_filter_projects_no_filters_returns_all():
    out = filter_projects(_SAMPLE_PROJECTS, includes=None, excludes=None)
    assert len(out) == len(_SAMPLE_PROJECTS)


def test_filter_include_single_pattern_keeps_matches():
    out = filter_projects(_SAMPLE_PROJECTS, includes=["^empirica"])
    names = {p["name"] for p in out}
    assert names == {"empirica", "empirica-extension"}


def test_filter_include_matches_against_path_too():
    """Pattern matching scratch directory should match by path even when name doesn't."""
    out = filter_projects(_SAMPLE_PROJECTS, includes=["scratch"])
    assert [p["name"] for p in out] == ["side-quest"]


def test_filter_multiple_includes_are_or():
    """Multi --include = OR. Project kept if ANY pattern matches."""
    out = filter_projects(_SAMPLE_PROJECTS, includes=["cortex", "side"])
    names = {p["name"] for p in out}
    assert names == {"cortex", "side-quest"}


def test_filter_exclude_drops_matches():
    out = filter_projects(_SAMPLE_PROJECTS, excludes=["archive"])
    names = {p["name"] for p in out}
    assert "old-project" not in names
    assert len(out) == 4


def test_filter_multiple_excludes_are_or():
    """Multi --exclude = OR. Project dropped if ANY pattern matches."""
    out = filter_projects(_SAMPLE_PROJECTS, excludes=["archive", "scratch"])
    names = {p["name"] for p in out}
    assert names == {"empirica", "empirica-extension", "cortex"}


def test_filter_exclude_runs_after_include():
    """Order matters: include narrows first, exclude trims further."""
    out = filter_projects(_SAMPLE_PROJECTS, includes=["^empirica"], excludes=["extension"])
    assert [p["name"] for p in out] == ["empirica"]


def test_filter_include_no_matches_returns_empty():
    out = filter_projects(_SAMPLE_PROJECTS, includes=["nonexistent"])
    assert out == []


def test_filter_handles_missing_name_or_path_fields():
    """Defensive: project entries with missing fields shouldn't crash."""
    projects = [
        {"name": "a", "path": "/x/a"},
        {"path": "/x/b"},  # name missing
        {"name": "c"},  # path missing
        {},  # both missing
    ]
    out = filter_projects(projects, includes=["a"])
    # 'a' matches name 'a' AND path '/x/a' (substring match)
    assert any(p.get("name") == "a" for p in out)


def test_filter_invalid_regex_raises_re_error():
    """Caller (handler) catches re.error and prints friendly message."""
    import re as _re

    try:
        filter_projects(_SAMPLE_PROJECTS, includes=["[unclosed"])
    except _re.error:
        return  # expected
    raise AssertionError("filter_projects should have raised re.error for invalid regex")
